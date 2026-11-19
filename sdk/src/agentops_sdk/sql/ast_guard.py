# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""AST-level SQL governance validator with fail-closed table allowlists."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlglot
from sqlglot import errors as sqlglot_errors
from sqlglot import exp
from sqlglot.optimizer import scope as sql_scope

from agentops_sdk.domain import models
from agentops_sdk.sql import _ast_rules

normalize_table_parts = _ast_rules.normalize_table_parts
is_table_in_allowlist = _ast_rules.is_table_in_allowlist


def collect_physical_tables(root: exp.Expr) -> list[exp.Table]:
    """Collects physical base tables across scopes, excluding CTE aliases."""
    root_scope = sql_scope.build_scope(root)
    if root_scope is None:
        return [
            t
            for t in root.find_all(exp.Table)
            if isinstance(t.this, exp.Identifier) and t.name
        ]
    tables: list[exp.Table] = []
    seen_ids: set[int] = set()
    for sc in root_scope.traverse():
        cte_names = {name.lower() for name in sc.cte_sources}
        candidates = list(sc.tables)
        if isinstance(sc.expression, exp.Table):
            candidates.append(sc.expression)
        for tbl in candidates:
            if id(tbl) in seen_ids:
                continue
            if not isinstance(tbl.this, exp.Identifier) or not tbl.name:
                continue
            if not tbl.catalog and not tbl.db and tbl.name.lower() in cte_names:
                continue
            seen_ids.add(id(tbl))
            tables.append(tbl)
    return tables


def _unwrap_relation_target(expr: exp.Expr | None) -> exp.Expr | None:
    cur = expr
    while isinstance(cur, (exp.Subquery, exp.Paren, exp.TableSample)):
        if isinstance(cur, exp.Subquery) and isinstance(cur.this, exp.Query):
            return cur
        cur = cur.this
    return cur


def _check_table_valued_function(node: exp.Expr) -> str | None:
    if isinstance(node, exp.Table) and isinstance(
        node.this, (exp.Func, exp.Anonymous)
    ):
        return f"Forbidden table-valued function: {node.this.sql_name()}."
    if isinstance(node, (exp.From, exp.Join)):
        target = _unwrap_relation_target(node.this)
        if isinstance(target, (exp.Func, exp.Anonymous)) and not isinstance(
            target, exp.Unnest
        ):
            return f"Forbidden table-valued function: {target.sql_name()}."
    return None


def _check_single_node(node: exp.Expr) -> str | None:
    for comment in node.comments or ():
        if _ast_rules._HIDDEN_DDL_COMMENT_RE.search(comment):
            return "SQL comment contains forbidden DDL or DML keywords."
    if isinstance(node, _ast_rules._FORBIDDEN_AST_NODES):
        return f"Forbidden SQL operation: {type(node).__name__}."
    if tvf_err := _check_table_valued_function(node):
        return tvf_err
    if isinstance(node, (exp.SessionUser, exp.CurrentUser)):
        return f"Forbidden SQL function: {type(node).__name__.upper()}."
    if (
        isinstance(node, exp.Column)
        and not node.table
        and isinstance(node.this, exp.Identifier)
        and not node.this.quoted
        and node.this.name.upper() in _ast_rules._FORBIDDEN_FUNCTIONS
    ):
        return f"Forbidden SQL identifier: {node.this.name.upper()}."
    if isinstance(node, exp.Func):
        names = {node.sql_name().upper()}
        if isinstance(node, exp.Anonymous) and node.name:
            names.add(node.name.upper())
        if hit := (names & _ast_rules._FORBIDDEN_FUNCTIONS):
            return f"Forbidden SQL function: {sorted(hit)[0]}."
    return None


def _check_comments_and_nodes(root: exp.Expr) -> str | None:
    """Returns a violation message if AST contains forbidden nodes or DDL."""
    for node in root.walk():
        err = _check_single_node(node)
        if err is not None:
            return err
    return None


def _check_tables(
    tables: Sequence[exp.Table], allowed_tables: Sequence[str] | None
) -> tuple[str | None, tuple[str, ...]]:
    """Checks system schemas/tables and optional fail-closed allowlists."""
    seen: list[str] = []
    for tbl in tables:
        parts = normalize_table_parts(tbl)
        if not parts:
            continue
        tid = ".".join(parts)
        if tid not in seen:
            seen.append(tid)
        if (
            any(seg in _ast_rules._SYSTEM_SCHEMAS for seg in parts[:-1])
            or parts[-1] in _ast_rules._SYSTEM_TABLES
        ):
            return (
                f"Access to restricted system schema or table in {tid!r}.",
                tuple(seen),
            )
        if allowed_tables is not None and not is_table_in_allowlist(
            parts, allowed_tables
        ):
            return (
                f"Unauthorized table access: {tid!r} is not allowlisted.",
                tuple(seen),
            )
    return None, tuple(seen)


def _parse_single_readonly(
    sql: Any, dialect: str
) -> tuple[exp.Expr | None, str | None]:
    if not isinstance(sql, str) or not sql.strip():
        return None, "SQL query must be a non-empty string."
    try:
        stmts = sqlglot.parse(sql, read=dialect)
    except sqlglot_errors.SqlglotError as exc:
        return None, f"SQL parse error: {exc}"
    if len(stmts) != 1 or stmts[0] is None:
        return None, "Multi-statement or empty SQL is forbidden."
    if not isinstance(stmts[0], _ast_rules._ALLOWED_ROOT_TYPES):
        return (
            None,
            f"Only read-only SELECT queries are permitted; "
            f"got {type(stmts[0]).__name__}.",
        )
    return stmts[0], None


class SQLASTValidator:
    """Read-only SQL AST governance validator."""

    def __init__(
        self,
        *,
        dialect: str = "bigquery",
        allowed_tables: Sequence[str] | None = None,
    ) -> None:
        self._dialect = dialect
        self._allowed_tables = (
            tuple(allowed_tables) if allowed_tables is not None else None
        )

    def validate(
        self,
        sql: Any,
        *,
        dialect: str | None = None,
        allowed_tables: Sequence[str] | None = None,
    ) -> models.SQLValidationResult:
        """Validates SQL read-only invariants and table allowlists."""
        active_dialect = dialect or self._dialect
        active_allow = (
            allowed_tables
            if allowed_tables is not None
            else self._allowed_tables
        )
        root, parse_err = _parse_single_readonly(sql, active_dialect)
        if root is None:
            return models.SQLValidationResult(False, "", parse_err)
        node_err = _check_comments_and_nodes(root)
        if node_err is not None:
            return models.SQLValidationResult(False, "", node_err)
        tbl_err, refs = _check_tables(
            collect_physical_tables(root), active_allow
        )
        if tbl_err is not None:
            return models.SQLValidationResult(False, "", tbl_err, refs)
        return models.SQLValidationResult(
            True, root.sql(dialect=active_dialect, comments=False), None, refs
        )


def validate_sql_query(
    sql: str,
    *,
    dialect: str = "bigquery",
    allowed_tables: Sequence[str] | None = None,
) -> models.SQLValidationResult:
    """Validates a SQL query against AST read-only and allowlist rules."""
    return SQLASTValidator(
        dialect=dialect, allowed_tables=allowed_tables
    ).validate(sql)
