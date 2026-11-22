# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""SQL AST constants and strict table allowlist matching helpers."""

from __future__ import annotations

from collections.abc import Sequence
import re

from sqlglot import exp
from sqlglot.dialects import dialect as sql_dialect
from sqlglot.optimizer import scope as sql_scope

_ALLOWED_ROOT_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except)
_FORBIDDEN_AST_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.Merge,
    exp.TruncateTable,
    exp.Command,
    exp.Grant,
    exp.Revoke,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Set,
    exp.LoadData,
    exp.Copy,
    exp.Into,
    exp.TableFromRows,
)
_SYSTEM_SCHEMAS = frozenset(
    {
        "information_schema",
        "pg_catalog",
        "pg_toast",
        "sys",
        "mysql",
        "performance_schema",
        "system",
    }
)
_SYSTEM_TABLES = frozenset(
    {
        "sqlite_master",
        "sqlite_schema",
        "sqlite_temp_master",
        "sqlite_temp_schema",
    }
)
_FORBIDDEN_FUNCTIONS = frozenset(
    {
        "SESSION_USER",
        "SYSTEM_USER",
        "CURRENT_USER",
        "EXTERNAL_QUERY",
        "LOAD_DATA",
        "LOAD_FILE",
        "PG_READ_FILE",
        "PG_READ_BINARY_FILE",
        "PG_LS_DIR",
        "PG_STAT_FILE",
        "PG_EXECUTE_SERVER_PROGRAM",
        "PG_SLEEP",
        "DBLINK",
        "LO_IMPORT",
        "LO_EXPORT",
    }
)
_HIDDEN_DDL_COMMENT_RE = re.compile(
    r"(?:!\d*|\b(?:DROP|ALTER|TRUNCATE|CREATE|DELETE|INSERT|UPDATE|GRANT"
    r"|REVOKE|MERGE|EXEC|EXECUTE)\b)",
    re.IGNORECASE,
)


def is_predict_model_table(table: exp.Table) -> bool:
    """Returns True if table is the MODEL argument of ML.PREDICT."""
    return isinstance(table.parent, exp.Predict) and table.parent.this is table


def resolve_identifier_name(
    ident: exp.Identifier, dialect_obj: sql_dialect.Dialect
) -> str:
    """Normalizes an Identifier using dialect case-folding rules."""
    normalized = dialect_obj.normalize_identifier(ident.copy())
    return str(normalized.name)


def _cte_identifier_name(
    cte_node: exp.CTE, dialect_obj: sql_dialect.Dialect
) -> str | None:
    alias = cte_node.args.get("alias")
    if isinstance(alias, exp.TableAlias) and isinstance(
        alias.this, exp.Identifier
    ):
        return resolve_identifier_name(alias.this, dialect_obj)
    return None


def visible_cte_names(
    sc: sql_scope.Scope, dialect_obj: sql_dialect.Dialect
) -> set[str]:
    """Returns dialect-normalized CTE names visible in a scope."""
    names: set[str] = set()
    for raw_name, cte_scope in sc.cte_sources.items():
        parent = cte_scope.expression.parent
        if isinstance(parent, exp.CTE):
            resolved = _cte_identifier_name(parent, dialect_obj)
            if resolved:
                names.add(resolved)
                continue
        names.add(
            resolve_identifier_name(exp.to_identifier(raw_name), dialect_obj)
        )
    return names


def _collect_with_ctes(
    with_node: exp.With,
    stop_cte: exp.Expr | None,
    dialect_obj: sql_dialect.Dialect,
    names: set[str],
) -> None:
    is_rec = bool(with_node.args.get("recursive"))
    for cte in with_node.expressions:
        if not isinstance(cte, exp.CTE):
            continue
        if cte is stop_cte:
            if is_rec and (name := _cte_identifier_name(cte, dialect_obj)):
                names.add(name)
            break
        if name := _cte_identifier_name(cte, dialect_obj):
            names.add(name)


def enclosing_cte_names(
    tbl: exp.Table, dialect_obj: sql_dialect.Dialect
) -> set[str]:
    """Walks ancestor WITH clauses respecting recursive CTE visibility."""
    names: set[str] = set()
    prev: exp.Expr = tbl
    cur: exp.Expr | None = tbl.parent
    while cur is not None:
        if isinstance(cur, exp.With):
            _collect_with_ctes(cur, prev, dialect_obj, names)
        else:
            with_node = cur.args.get("with_")
            if isinstance(with_node, exp.With) and prev is not with_node:
                _collect_with_ctes(with_node, None, dialect_obj, names)
        prev = cur
        cur = cur.parent
    return names


def split_identifier_segments(raw: str) -> tuple[str, ...]:
    """Splits a dot-separated identifier path into normalized segments."""
    return tuple(
        seg.strip("`\"'[] ").lower()
        for seg in raw.strip().split(".")
        if seg.strip("`\"'[] ")
    )


def normalize_table_parts(table: exp.Table) -> tuple[str, ...]:
    """Extracts normalized lowercase path parts from a SQL AST table."""
    parts: list[str] = []
    for raw in (table.catalog, table.db, table.name):
        if not raw:
            continue
        parts.extend(split_identifier_segments(raw))
    return tuple(parts)


def _normalize_pattern(pattern: str) -> str:
    return ".".join(split_identifier_segments(pattern))


def matches_rls_pattern(parts: tuple[str, ...], pattern: str) -> bool:
    """Checks wildcard RLS target patterns (*, dataset.*, project.ds.*)."""
    pat = _normalize_pattern(pattern)
    if not pat or not parts:
        return False
    if pat == "*":
        return True
    if pat.endswith(".*"):
        prefix = tuple(pat[:-2].split("."))
        return (
            bool(prefix)
            and len(parts) == len(prefix) + 1
            and parts[:-1] == prefix
        )
    return False


def is_table_in_allowlist(
    table_parts: tuple[str, ...],
    allowed_tables: Sequence[str],
) -> bool:
    """Checks strict exact or anchored prefix match (CWE-636)."""
    if not allowed_tables or not table_parts:
        return False
    table_id = ".".join(table_parts)
    for raw_pat in allowed_tables:
        pat = _normalize_pattern(raw_pat)
        if not pat or pat == "*":
            continue
        if pat.endswith(".*"):
            prefix = tuple(pat[:-2].split("."))
            if (
                prefix
                and len(table_parts) == len(prefix) + 1
                and table_parts[:-1] == prefix
            ):
                return True
        elif table_id == pat:
            return True
    return False
