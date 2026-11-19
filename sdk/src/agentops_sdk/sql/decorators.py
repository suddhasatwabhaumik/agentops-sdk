# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Sync/async SQL governance decorator enforcing AST validation on all args."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import inspect
from typing import Any, ParamSpec, TypeVar

from opentelemetry import trace

from agentops_sdk.domain import exceptions
from agentops_sdk.domain import lifecycle
from agentops_sdk.sql import ast_guard

_P = ParamSpec("_P")
_R = TypeVar("_R")

_DEFAULT_SQL_PARAMS: tuple[str, ...] = (
    "sql",
    "query",
    "query_template",
    "sql_query",
    "sql_text",
    "statement",
)


def _record_span_violation(reason: str) -> None:
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("sql.ast.is_safe", False)
        span.set_status(trace.Status(trace.StatusCode.ERROR, reason))


def _record_span_safe() -> None:
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("sql.ast.is_safe", True)


def _check_one_sql(
    validator: ast_guard.SQLASTValidator,
    param_name: str,
    raw_value: Any,
    *,
    auto_sanitize: bool,
) -> Any:
    res = validator.validate(raw_value)
    if not res.is_safe:
        msg = res.violation_reason or f"Unsafe SQL in {param_name!r}."
        _record_span_violation(msg)
        raise exceptions.SQLGovernanceError(msg)
    return res.sanitized_sql if auto_sanitize else raw_value


def _find_var_param(
    sig: inspect.Signature, kind: inspect._ParameterKind
) -> str | None:
    for param in sig.parameters.values():
        if param.kind == kind:
            return param.name
    return None


def _inspect_var_positional(
    val: tuple[Any, ...],
    var_name: str,
    target_params: set[str],
    validator: ast_guard.SQLASTValidator,
    *,
    auto_sanitize: bool,
) -> tuple[tuple[Any, ...], int]:
    include_all = var_name in target_params
    updated: list[Any] = []
    inspected = 0
    for idx, item in enumerate(val):
        if include_all or isinstance(item, str):
            inspected += 1
            updated.append(
                _check_one_sql(
                    validator,
                    f"{var_name}[{idx}]",
                    item,
                    auto_sanitize=auto_sanitize,
                )
            )
        else:
            updated.append(item)
    return tuple(updated), inspected


def _assert_inspected_count(
    inspected: int, explicit_params: bool, target_count: int
) -> None:
    if explicit_params and inspected < target_count:
        msg = "Missing required SQL parameter for AST validation."
        _record_span_violation(msg)
        raise exceptions.SQLGovernanceError(msg)
    if inspected == 0:
        msg = "No SQL parameter found for AST validation."
        _record_span_violation(msg)
        raise exceptions.SQLGovernanceError(msg)
    _record_span_safe()


def _validate_bound_arguments(
    sig: inspect.Signature,
    validator: ast_guard.SQLASTValidator,
    target_params: set[str],
    explicit_params: bool,
    auto_sanitize: bool,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Validates every matched SQL parameter in args, defaults, and kwargs."""
    bound = sig.bind_partial(*args, **kwargs)
    bound.apply_defaults()
    var_kw = _find_var_param(sig, inspect.Parameter.VAR_KEYWORD)
    var_pos = _find_var_param(sig, inspect.Parameter.VAR_POSITIONAL)
    inspected = 0
    for name, val in list(bound.arguments.items()):
        if name == var_kw and isinstance(val, dict):
            for key in [k for k in val if k in target_params]:
                inspected += 1
                val[key] = _check_one_sql(
                    validator, key, val[key], auto_sanitize=auto_sanitize
                )
        elif name == var_pos and isinstance(val, tuple):
            new_tuple, count = _inspect_var_positional(
                val, name, target_params, validator, auto_sanitize=auto_sanitize
            )
            bound.arguments[name] = new_tuple
            inspected += count
        elif name in target_params:
            inspected += 1
            bound.arguments[name] = _check_one_sql(
                validator, name, val, auto_sanitize=auto_sanitize
            )
    _assert_inspected_count(inspected, explicit_params, len(target_params))
    return bound.args, bound.kwargs


def _resolve_sql_params(
    sql_params: Sequence[str] | None,
) -> tuple[set[str], bool]:
    if sql_params is None:
        return set(_DEFAULT_SQL_PARAMS), False
    cleaned = [
        p.strip() for p in sql_params if isinstance(p, str) and p.strip()
    ]
    if not cleaned:
        raise exceptions.SQLGovernanceError(
            "sql_params must not be empty when explicitly provided."
        )
    return set(cleaned), True


def validate_sql_query_decorator(
    *,
    dialect: str = "bigquery",
    allowed_tables: Sequence[str] | None = None,
    sql_params: Sequence[str] | None = None,
    auto_sanitize: bool = True,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Validates all SQL parameters on sync/async callables via AST."""
    target_set, explicit = _resolve_sql_params(sql_params)
    validator = ast_guard.SQLASTValidator(
        dialect=dialect, allowed_tables=allowed_tables
    )

    def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
        sig = inspect.signature(func)

        def _on_enter(
            args: tuple[Any, ...],
            kwargs: dict[str, Any],
        ) -> tuple[None, tuple[Any, ...], dict[str, Any]]:
            new_args, new_kwargs = _validate_bound_arguments(
                sig,
                validator,
                target_set,
                explicit,
                auto_sanitize,
                args,
                kwargs,
            )
            return None, new_args, new_kwargs

        return lifecycle.bind_sync_or_async(func, on_enter=_on_enter)

    return decorator
