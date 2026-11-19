# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""AST-level Row-Level Security (RLS) policy rewriter for multi-tenant SQL."""

from __future__ import annotations

import collections
from collections.abc import Mapping, Sequence
from typing import Any

import sqlglot
from sqlglot import errors as sqlglot_errors
from sqlglot import exp
from sqlglot.optimizer import scope as sql_scope

from agentops_sdk.domain import exceptions
from agentops_sdk.sql import _rls_helpers
from agentops_sdk.sql import ast_guard

_ADMIN_ROLES = frozenset({"admin", "superadmin"})
_DEFAULT_POLICY_COLUMNS: Mapping[str, str] = {"tenant_id": "tenant_id"}


def _direct_physical_tables(
    select_node: exp.Select,
    cte_names: set[str],
    target_tables: Sequence[str] | None,
) -> list[tuple[exp.Table, str | None]]:
    """Collects physical base tables in FROM and JOINs of a single SELECT."""
    candidates: list[tuple[exp.Table, str | None]] = []
    from_clause = select_node.args.get("from_")
    if from_clause is not None:
        tbl, alias = _rls_helpers.unwrap_table_node(from_clause.this)
        if tbl is not None:
            candidates.append((tbl, alias))
    for join_node in select_node.args.get("joins") or ():
        tbl, alias = _rls_helpers.unwrap_table_node(join_node.this)
        if tbl is not None:
            candidates.append((tbl, alias))
    restricted: list[tuple[exp.Table, str | None]] = []
    for tbl, alias in candidates:
        if not isinstance(tbl.this, exp.Identifier) or not tbl.name:
            continue
        if not tbl.catalog and not tbl.db and tbl.name.lower() in cte_names:
            continue
        if _rls_helpers.matches_target_tables(tbl, target_tables):
            restricted.append((tbl, alias))
    return restricted


def _build_qualified_column(
    tbl: exp.Table,
    outer_alias: str | None,
    col_name: str,
    name_counts: Mapping[str, int],
) -> exp.Column:
    short = outer_alias or tbl.alias_or_name
    if (
        name_counts.get(short.lower(), 0) > 1
        and not outer_alias
        and not tbl.alias
        and (tbl.db or tbl.catalog)
    ):
        return exp.column(
            col_name,
            table=tbl.name,
            db=tbl.db or None,
            catalog=tbl.catalog or None,
        )
    return exp.column(col_name, table=short)


def _attach_table_predicates(
    select_node: exp.Select,
    tables: Sequence[tuple[exp.Table, str | None]],
    col_filters: Sequence[tuple[str, Any]],
) -> None:
    """Attaches parenthesized alias-qualified WHERE predicates to SELECT."""
    name_counts = collections.Counter(
        (alias or tbl.alias_or_name).lower() for tbl, alias in tables
    )
    preds: list[exp.Condition] = []
    for tbl, outer_alias in tables:
        for col_name, literal_or_list in col_filters:
            col_expr = _build_qualified_column(
                tbl, outer_alias, col_name, name_counts
            )
            if isinstance(literal_or_list, list):
                cond: exp.Condition = exp.In(
                    this=col_expr,
                    expressions=[x.copy() for x in literal_or_list],
                )
            else:
                cond = exp.EQ(this=col_expr, expression=literal_or_list.copy())
            preds.append(exp.Paren(this=cond))
    if not preds:
        return
    combined: exp.Condition = preds[0]
    for extra in preds[1:]:
        combined = exp.and_(combined, extra, wrap=False)
    where_node = select_node.args.get("where")
    if where_node is not None and where_node.this is not None:
        existing: exp.Expression = where_node.this
        if not isinstance(existing, exp.Paren):
            existing = exp.Paren(this=existing)
        select_node.set(
            "where", exp.Where(this=exp.and_(existing, combined, wrap=False))
        )
    else:
        select_node.set("where", exp.Where(this=combined))


def _rewrite_ast_selects(
    sql: str,
    dialect: str,
    col_filters: Sequence[tuple[str, Any]],
    target_tables: Sequence[str] | None,
) -> str:
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot_errors.SqlglotError as exc:
        raise exceptions.SQLGovernanceError(
            f"Failed to parse SQL for RLS rewriting: {exc}"
        ) from exc
    root_scope = sql_scope.build_scope(tree)
    scope_map = (
        {id(sc.expression): sc for sc in root_scope.traverse()}
        if root_scope is not None
        else {}
    )
    for sel in tree.find_all(exp.Select):
        sc = scope_map.get(id(sel))
        ctes = {k.lower() for k in sc.cte_sources} if sc is not None else set()
        phys = _direct_physical_tables(sel, ctes, target_tables)
        _attach_table_predicates(sel, phys, col_filters)
    return tree.sql(dialect=dialect, comments=False)


class RowLevelPolicyEngine:
    """Rewrites SQL ASTs with tenant Row-Level Security WHERE predicates."""

    def __init__(
        self,
        *,
        dialect: str = "bigquery",
        policy_columns: Mapping[str, str] | None = None,
        target_tables: Sequence[str] | None = None,
    ) -> None:
        self._dialect = dialect
        self._policy_columns: Mapping[str, str] = (
            dict(policy_columns)
            if policy_columns is not None
            else dict(_DEFAULT_POLICY_COLUMNS)
        )
        self._target_tables = (
            tuple(target_tables) if target_tables is not None else None
        )

    def apply(
        self,
        sql: str,
        claims: Mapping[str, Any],
        *,
        dialect: str | None = None,
        policy_columns: Mapping[str, str] | None = None,
        target_tables: Sequence[str] | None = None,
    ) -> str:
        """Validates read-only SQL and injects RLS predicates across SELECTs."""
        active_dialect = dialect or self._dialect
        validation = ast_guard.validate_sql_query(sql, dialect=active_dialect)
        if not validation.is_safe:
            raise exceptions.SQLGovernanceError(
                validation.violation_reason or "Unsafe SQL rejected before RLS."
            )
        role = str((claims or {}).get("role", "")).strip().lower()
        if role in _ADMIN_ROLES:
            return validation.sanitized_sql
        cols = (
            policy_columns
            if policy_columns is not None
            else self._policy_columns
        )
        targets = (
            target_tables if target_tables is not None else self._target_tables
        )
        filters = _rls_helpers.resolve_column_filters(claims, cols)
        return _rewrite_ast_selects(
            validation.sanitized_sql, active_dialect, filters, targets
        )


def apply_row_level_policy(
    sql: str,
    claims: Mapping[str, Any],
    *,
    dialect: str = "bigquery",
    policy_columns: Mapping[str, str] | None = None,
    target_tables: Sequence[str] | None = None,
) -> str:
    """Rewrites SQL with table-qualified parenthesized RLS predicates."""
    return RowLevelPolicyEngine(
        dialect=dialect,
        policy_columns=policy_columns,
        target_tables=target_tables,
    ).apply(sql, claims)
