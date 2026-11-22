# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""RLS claim literal conversion, target matching, and AST predicate helpers."""

from __future__ import annotations

import collections
from collections.abc import Mapping, Sequence
import re
from typing import Any

from sqlglot import exp
from sqlglot.optimizer import scope as sql_scope

from agentops_sdk.domain import exceptions
from agentops_sdk.sql import _ast_rules
from agentops_sdk.sql import ast_guard

_COL_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WRAPPER_TYPES = (exp.Subquery, exp.Paren, exp.TableSample)


def literal_for_scalar(value: Any) -> exp.Expr:
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


def validate_target_tables(
    target_tables: Sequence[str] | None,
) -> tuple[str, ...] | None:
    """Validates non-None target_tables and fails closed when empty."""
    if target_tables is None:
        return None
    cleaned = tuple(
        t.strip()
        for t in target_tables
        if isinstance(t, str) and _ast_rules.split_identifier_segments(t)
    )
    if not cleaned:
        raise exceptions.SQLGovernanceError(
            "RLS target_tables must not be empty when provided."
        )
    return cleaned


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
    if any(_ast_rules.matches_rls_pattern(parts, pat) for pat in target_tables):
        return True
    base_name = parts[-1]
    for pat in target_tables:
        segs = _ast_rules.split_identifier_segments(pat)
        if not segs or "*" in segs:
            continue
        if len(parts) == 1 and segs[-1] == base_name:
            return True
        if len(segs) == 1 and segs[0] == base_name:
            return True
    return False


def validate_rls_relation_modifiers(tbl: exp.Table) -> None:
    """Rejects PIVOT/UNPIVOT and column-list aliases on RLS targets."""
    cur: exp.Expr | None = tbl
    while isinstance(cur, (exp.Table, exp.Join, *_WRAPPER_TYPES)):
        if cur.args.get("pivots"):
            raise exceptions.SQLGovernanceError(
                "PIVOT/UNPIVOT on RLS-protected tables is not supported."
            )
        alias = cur.args.get("alias")
        if isinstance(alias, exp.TableAlias) and alias.args.get("columns"):
            raise exceptions.SQLGovernanceError(
                "Column-list aliases on RLS-protected tables are forbidden."
            )
        cur = cur.parent


def _alias_ident(node: exp.Expr) -> exp.Identifier | None:
    alias = node.args.get("alias")
    if isinstance(alias, exp.TableAlias) and isinstance(
        alias.this, exp.Identifier
    ):
        return alias.this.copy()
    return None


def resolve_alias_identifier(tbl: exp.Table) -> exp.Identifier | None:
    """Resolves outer wrapper or direct table alias Identifier."""
    outer: exp.Identifier | None = None
    prev: exp.Expr = tbl
    cur = tbl.parent
    while isinstance(cur, _WRAPPER_TYPES) and cur.this is prev:
        if ident := _alias_ident(cur):
            outer = ident
        prev = cur
        cur = cur.parent
    return outer or _alias_ident(tbl)


def _select_relation_nodes(select_node: exp.Select) -> list[exp.Expr | None]:
    nodes: list[exp.Expr | None] = []
    if (from_clause := select_node.args.get("from_")) is not None:
        nodes.append(from_clause.this)
    for join_node in select_node.args.get("joins") or ():
        nodes.append(join_node.this)
    return nodes


def collect_scope_candidates(sc: sql_scope.Scope) -> list[exp.Table]:
    """Collects scope tables plus parenthesized FROM/JOIN table targets."""
    candidates = list(sc.tables)
    seen_ids = {id(t) for t in candidates}
    if not isinstance(sc.expression, exp.Select):
        return candidates
    for node in _select_relation_nodes(sc.expression):
        target = ast_guard.unwrap_relation_target(node)
        if isinstance(target, exp.Table) and id(target) not in seen_ids:
            seen_ids.add(id(target))
            candidates.append(target)
    return candidates


def count_scope_short_names(
    sc: sql_scope.Scope, candidates: Sequence[exp.Table]
) -> collections.Counter[str]:
    """Counts short relation names in scope for qualifier disambiguation."""
    counts: collections.Counter[str] = collections.Counter()
    for tbl in candidates:
        alias_ident = resolve_alias_identifier(tbl)
        short = alias_ident.name if alias_ident is not None else tbl.name
        if short:
            counts[short.lower()] += 1
    if isinstance(sc.expression, exp.Select):
        for node in _select_relation_nodes(sc.expression):
            target = ast_guard.unwrap_relation_target(node)
            if isinstance(target, exp.Subquery) and target.alias:
                counts[target.alias.lower()] += 1
    return counts


def build_qualified_column(
    tbl: exp.Table,
    col_name: str,
    name_counts: Mapping[str, int],
) -> exp.Column:
    """Builds a qualified Column AST node, preserving quoted identifiers."""
    alias_ident = resolve_alias_identifier(tbl)
    if alias_ident is not None:
        return exp.Column(this=exp.to_identifier(col_name), table=alias_ident)
    short = tbl.name
    table_ident = (
        tbl.this.copy()
        if isinstance(tbl.this, exp.Identifier)
        else exp.to_identifier(short)
    )
    if name_counts.get(short.lower(), 0) > 1 and (tbl.db or tbl.catalog):
        db_n, cat_n = tbl.args.get("db"), tbl.args.get("catalog")
        return exp.Column(
            this=exp.to_identifier(col_name),
            table=table_ident,
            db=db_n.copy() if isinstance(db_n, exp.Identifier) else None,
            catalog=cat_n.copy() if isinstance(cat_n, exp.Identifier) else None,
        )
    return exp.Column(this=exp.to_identifier(col_name), table=table_ident)


def find_enclosing_left_join(tbl: exp.Table) -> exp.Join | None:
    """Returns enclosing LEFT JOIN without USING/NATURAL on right side."""
    prev: exp.Expr = tbl
    cur = tbl.parent
    while isinstance(cur, _WRAPPER_TYPES) and cur.this is prev:
        prev = cur
        cur = cur.parent
    if (
        not isinstance(cur, exp.Join)
        or cur.this is not prev
        or str(cur.side or "").upper() != "LEFT"
        or cur.args.get("using")
        or str(cur.args.get("method") or "").upper() == "NATURAL"
    ):
        return None
    return cur


def _merge_and(
    existing: exp.Expr | None, extra: exp.Condition
) -> exp.Condition:
    if not isinstance(existing, exp.Expr):
        return extra
    wrapped = (
        existing
        if isinstance(existing, exp.Paren)
        else exp.Paren(this=existing)
    )
    return exp.and_(wrapped, extra, wrap=False, copy=False)


def attach_left_join_predicate(
    join_node: exp.Join, predicate: exp.Condition
) -> None:
    """Merges an RLS predicate into a LEFT JOIN's ON clause."""
    join_node.set("on", _merge_and(join_node.args.get("on"), predicate))


def build_column_predicate(
    col_expr: exp.Column, literal_or_list: Any
) -> exp.Condition:
    """Builds a parenthesized equality or IN predicate for a column."""
    if isinstance(literal_or_list, list):
        cond: exp.Condition = exp.In(
            this=col_expr,
            expressions=[x.copy() for x in literal_or_list],
        )
    else:
        cond = exp.EQ(this=col_expr, expression=literal_or_list.copy())
    return exp.Paren(this=cond)


def attach_where_predicates(
    select_node: exp.Select, preds: Sequence[exp.Condition]
) -> None:
    """ANDs predicates onto a SELECT's WHERE clause, wrapping existing."""
    if not preds:
        return
    combined: exp.Condition = preds[0]
    for extra in preds[1:]:
        combined = exp.and_(combined, extra, wrap=False, copy=False)
    where_node = select_node.args.get("where")
    existing = where_node.this if where_node is not None else None
    select_node.set("where", exp.Where(this=_merge_and(existing, combined)))
