# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""AST-level Row-Level Security (RLS) policy rewriter for multi-tenant SQL."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import sqlglot
from sqlglot import errors as sqlglot_errors
from sqlglot import exp
from sqlglot.dialects import dialect as sql_dialect
from sqlglot.optimizer import scope as sql_scope

from agentops_sdk.domain import exceptions
from agentops_sdk.sql import _ast_rules
from agentops_sdk.sql import _rls_helpers
from agentops_sdk.sql import ast_guard

_ADMIN_ROLES = frozenset({"admin", "superadmin"})
_DEFAULT_POLICY_COLUMNS: Mapping[str, str] = {"tenant_id": "tenant_id"}


def _classify_scope_tables(
    sc: sql_scope.Scope,
    candidates: Sequence[exp.Table],
    dialect_obj: sql_dialect.Dialect,
    target_tables: Sequence[str] | None,
) -> tuple[list[exp.Table], list[exp.Table], set[int]]:
    """Classifies scope candidates into physical tables, TVFs, and CTE refs."""
    cte_names = _ast_rules.visible_cte_names(sc, dialect_obj)
    physical: list[exp.Table] = []
    tvfs: list[exp.Table] = []
    cte_refs: set[int] = set()
    for tbl in candidates:
        if _ast_rules.is_predict_model_table(tbl):
            continue
        if isinstance(tbl.this, (exp.Func, exp.Anonymous)):
            tvfs.append(tbl)
            continue
        if not isinstance(tbl.this, exp.Identifier) or not tbl.name:
            continue
        if isinstance(tbl.parent, exp.Predict):
            continue
        all_ctes = cte_names | _ast_rules.enclosing_cte_names(tbl, dialect_obj)
        norm = _ast_rules.resolve_identifier_name(tbl.this, dialect_obj)
        if not tbl.catalog and not tbl.db and norm in all_ctes:
            cte_refs.add(id(tbl))
            continue
        if _rls_helpers.matches_target_tables(tbl, target_tables):
            physical.append(tbl)
    return physical, tvfs, cte_refs


def _find_joined_tvf(select_node: exp.Select) -> exp.Expr | None:
    """Returns any joined table-valued function expression in a SELECT."""
    for join_node in select_node.args.get("joins") or ():
        target = ast_guard.unwrap_relation_target(join_node.this)
        if isinstance(target, exp.Table) and isinstance(
            target.this, (exp.Func, exp.Anonymous)
        ):
            return target.this
        if isinstance(target, (exp.Func, exp.Anonymous)) and not isinstance(
            target, exp.Unnest
        ):
            return target
    return None


def _build_sole_function_predicate(
    tvf_tbl: exp.Table,
    col_filters: Sequence[tuple[str, Any]],
    target_tables: Sequence[str] | None,
    filtered: set[int],
) -> list[exp.Condition]:
    """Builds outer output predicates for sole-FROM ML.PREDICT queries."""
    if not isinstance(tvf_tbl.this, exp.Predict):
        return []
    targets_protected = False
    for inner in tvf_tbl.this.find_all(exp.Table):
        if _ast_rules.is_predict_model_table(inner):
            continue
        if not isinstance(inner.this, exp.Identifier) or not inner.name:
            continue
        if _rls_helpers.matches_target_tables(inner, target_tables):
            targets_protected = True
            if inner.parent is tvf_tbl.this:
                filtered.add(id(inner))
    if not targets_protected:
        return []
    alias_ident = _rls_helpers.resolve_alias_identifier(tvf_tbl)
    preds: list[exp.Condition] = []
    for col_name, literal_or_list in col_filters:
        col_expr = exp.Column(
            this=exp.to_identifier(col_name), table=alias_ident
        )
        preds.append(
            _rls_helpers.build_column_predicate(col_expr, literal_or_list)
        )
    return preds


def _attach_physical_predicates(
    tbl: exp.Table,
    col_filters: Sequence[tuple[str, Any]],
    name_counts: Mapping[str, int],
    where_preds: list[exp.Condition],
    filtered: set[int],
) -> None:
    _rls_helpers.validate_rls_relation_modifiers(tbl)
    left_join = _rls_helpers.find_enclosing_left_join(tbl)
    for col_name, literal_or_list in col_filters:
        col_expr = _rls_helpers.build_qualified_column(
            tbl, col_name, name_counts
        )
        pred = _rls_helpers.build_column_predicate(col_expr, literal_or_list)
        if left_join is not None:
            _rls_helpers.attach_left_join_predicate(left_join, pred)
        else:
            where_preds.append(pred)
    filtered.add(id(tbl))


def _apply_select_scope(
    sc: sql_scope.Scope,
    dialect_obj: sql_dialect.Dialect,
    col_filters: Sequence[tuple[str, Any]],
    target_tables: Sequence[str] | None,
    filtered: set[int],
    cte_references: set[int],
) -> None:
    """Applies RLS predicates to a single SELECT scope."""
    select_node = sc.expression
    if not isinstance(select_node, exp.Select):
        return
    if _find_joined_tvf(select_node) is not None:
        raise exceptions.SQLGovernanceError(
            "Joined table-valued functions are forbidden under RLS."
        )
    candidates = _rls_helpers.collect_scope_candidates(sc)
    name_counts = _rls_helpers.count_scope_short_names(sc, candidates)
    physical, tvfs, cte_refs = _classify_scope_tables(
        sc, candidates, dialect_obj, target_tables
    )
    cte_references.update(cte_refs)
    where_preds: list[exp.Condition] = []
    for tvf_tbl in tvfs:
        where_preds.extend(
            _build_sole_function_predicate(
                tvf_tbl, col_filters, target_tables, filtered
            )
        )
    for tbl in physical:
        _attach_physical_predicates(
            tbl, col_filters, name_counts, where_preds, filtered
        )
    _rls_helpers.attach_where_predicates(select_node, where_preds)


def _verify_all_tables_covered(
    tree: exp.Expr,
    target_tables: Sequence[str] | None,
    covered_ids: set[int],
) -> None:
    """Fails closed if any target physical table escaped RLS filtering."""
    for tbl in tree.find_all(exp.Table):
        if not isinstance(tbl.this, exp.Identifier) or not tbl.name:
            continue
        if _ast_rules.is_predict_model_table(tbl):
            continue
        if id(tbl) in covered_ids:
            continue
        if _rls_helpers.matches_target_tables(tbl, target_tables):
            raise exceptions.SQLGovernanceError(
                f"RLS policy failed to cover table {tbl.name!r}."
            )


def _rewrite_ast_selects(
    sql: str,
    dialect: str,
    col_filters: Sequence[tuple[str, Any]],
    target_tables: Sequence[str] | None,
) -> str:
    targets = _rls_helpers.validate_target_tables(target_tables)
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot_errors.SqlglotError as exc:
        raise exceptions.SQLGovernanceError(
            f"Failed to parse SQL for RLS rewriting: {exc}"
        ) from exc
    dialect_obj = sql_dialect.Dialect.get_or_raise(dialect)
    filtered: set[int] = set()
    cte_references: set[int] = set()
    for sc in sql_scope.traverse_scope(tree):
        _apply_select_scope(
            sc, dialect_obj, col_filters, targets, filtered, cte_references
        )
    _verify_all_tables_covered(tree, targets, filtered | cte_references)
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
        self._target_tables = _rls_helpers.validate_target_tables(target_tables)

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
        targets = (
            _rls_helpers.validate_target_tables(target_tables)
            if target_tables is not None
            else self._target_tables
        )
        role = str((claims or {}).get("role", "")).strip().lower()
        if role in _ADMIN_ROLES:
            return validation.sanitized_sql
        cols = (
            policy_columns
            if policy_columns is not None
            else self._policy_columns
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
