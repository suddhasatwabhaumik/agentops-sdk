# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""SQL AST constants and strict table allowlist matching helpers."""

from __future__ import annotations

from collections.abc import Sequence
import re

from sqlglot import exp

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


def normalize_table_parts(table: exp.Table) -> tuple[str, ...]:
    """Extracts normalized lowercase path parts from a SQL AST table."""
    parts: list[str] = []
    for raw in (table.catalog, table.db, table.name):
        if not raw:
            continue
        for seg in raw.split("."):
            cleaned = seg.strip("`\"'[] ").lower()
            if cleaned:
                parts.append(cleaned)
    return tuple(parts)


def _normalize_pattern(pattern: str) -> str:
    segs = [
        s.strip("`\"'[] ").lower()
        for s in pattern.strip().split(".")
        if s.strip("`\"'[] ")
    ]
    return ".".join(segs)


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
