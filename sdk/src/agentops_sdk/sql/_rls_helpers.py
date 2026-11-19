# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""RLS claim literal conversion and target table matching helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from sqlglot import exp

from agentops_sdk.domain import exceptions
from agentops_sdk.sql import ast_guard

_COL_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def literal_for_scalar(value: Any) -> exp.Expression:
    """Converts a validated scalar tenant claim into a safe SQL AST literal."""
    if isinstance(value, bool) or value is None:
        raise exceptions.SQLGovernanceError(
            "Boolean or null values are invalid for RLS tenant claims."
        )
    if isinstance(value, int):
        return exp.Literal.number(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            raise exceptions.SQLGovernanceError(
                "Tenant claim string value must not be empty."
            )
        return exp.Literal.string(cleaned)
    raise exceptions.SQLGovernanceError(
        f"Unsupported tenant claim value type: {type(value).__name__}."
    )


def resolve_column_filters(
    claims: Mapping[str, Any],
    policy_columns: Mapping[str, str],
) -> list[tuple[str, Any]]:
    """Validates and extracts (sql_column, claim_value) pairs from claims."""
    if not policy_columns:
        raise exceptions.SQLGovernanceError(
            "RLS policy_columns mapping must not be empty."
        )
    if not claims:
        raise exceptions.SQLGovernanceError(
            "Non-admin claims missing required tenant isolation attributes."
        )
    resolved: list[tuple[str, Any]] = []
    for claim_key, col_name in policy_columns.items():
        if not _COL_IDENT_RE.match(col_name):
            raise exceptions.SQLGovernanceError(
                f"Invalid RLS SQL column identifier: {col_name!r}."
            )
        if claim_key not in claims:
            raise exceptions.SQLGovernanceError(
                f"Missing required tenant claim {claim_key!r} for RLS."
            )
        raw_val = claims[claim_key]
        if isinstance(raw_val, Sequence) and not isinstance(
            raw_val, (str, bytes)
        ):
            items = [literal_for_scalar(v) for v in raw_val]
            if not items:
                raise exceptions.SQLGovernanceError(
                    f"Tenant claim {claim_key!r} sequence must not be empty."
                )
            resolved.append((col_name, items))
        else:
            resolved.append((col_name, literal_for_scalar(raw_val)))
    return resolved


def matches_target_tables(
    tbl: exp.Table,
    target_tables: Sequence[str] | None,
) -> bool:
    """Returns True when tbl requires RLS predicate injection."""
    if target_tables is None:
        return True
    parts = ast_guard.normalize_table_parts(tbl)
    if not parts:
        return False
    if ast_guard.is_table_in_allowlist(parts, target_tables):
        return True
    base_name = parts[-1]
    for pat in target_tables:
        segs = [
            s.strip("`\"'[] ").lower()
            for s in pat.strip().split(".")
            if s.strip("`\"'[] ")
        ]
        if not segs or "*" in segs:
            continue
        if len(parts) == 1 and segs[-1] == base_name:
            return True
        if len(segs) == 1 and segs[0] == base_name:
            return True
    return False


def unwrap_table_node(
    expr: exp.Expr | None,
) -> tuple[exp.Table | None, str | None]:
    """Unwraps Subquery/TableSample/Paren wrappers around a physical Table."""
    outer_alias: str | None = None
    cur = expr
    while cur is not None:
        if isinstance(cur, exp.Table):
            return cur, outer_alias
        if isinstance(cur, exp.Subquery):
            if isinstance(cur.this, exp.Query):
                return None, None
            if not outer_alias and cur.alias:
                outer_alias = cur.alias
            cur = cur.this
            continue
        if isinstance(cur, (exp.TableSample, exp.Paren)):
            raw_alias = getattr(cur, "alias", "")
            if not outer_alias and raw_alias:
                outer_alias = str(raw_alias)
            cur = cur.this
            continue
        return None, None
    return None, None
