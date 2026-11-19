# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Output schema and custom assertion validator with bounded retry backoff."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
import dataclasses
import functools
import inspect
import json
import random
import time
from typing import Any, NoReturn, ParamSpec, TypeVar

from opentelemetry import trace
import pydantic

from agentops_sdk.domain import exceptions
from agentops_sdk.domain import validation

_P = ParamSpec("_P")
_R = TypeVar("_R")


class _OutputAssertionError(Exception):
    """Internal marker raised when assertion_callback returns False."""


@dataclasses.dataclass(frozen=True, slots=True)
class _ValidationPolicy:
    schema_model: type[pydantic.BaseModel] | None
    assertion_callback: Callable[[Any], bool | Awaitable[bool]] | None
    total_attempts: int
    retry_delay_seconds: float
    backoff_factor: float
    jitter_seconds: float
    retry_on_exceptions: tuple[type[BaseException], ...]


def _validate_single_item(
    item: Any, schema_model: type[pydantic.BaseModel]
) -> None:
    if isinstance(item, pydantic.BaseModel):
        schema_model.model_validate(item.model_dump())
    else:
        schema_model.model_validate(item)


def _validate_against_schema(
    output: Any, schema_model: type[pydantic.BaseModel]
) -> None:
    payload = (
        json.loads(output)
        if isinstance(output, (str, bytes, bytearray))
        else output
    )
    if isinstance(payload, (Mapping, pydantic.BaseModel)):
        _validate_single_item(payload, schema_model)
        return
    if isinstance(payload, Sequence) and not isinstance(
        payload, (str, bytes, bytearray)
    ):
        for item in payload:
            _validate_single_item(item, schema_model)
        return
    schema_model.model_validate(payload)


def _check_sync_output(output: Any, policy: _ValidationPolicy) -> None:
    if policy.schema_model is not None:
        _validate_against_schema(output, policy.schema_model)
    if policy.assertion_callback is not None:
        verdict = policy.assertion_callback(output)
        if inspect.isawaitable(verdict):
            if inspect.iscoroutine(verdict):
                verdict.close()
            raise TypeError(
                "Async assertion_callback cannot be used with a sync tool."
            )
        if not verdict:
            raise _OutputAssertionError(
                "Output validation assertion_callback returned False."
            )


async def _check_async_output(output: Any, policy: _ValidationPolicy) -> None:
    if policy.schema_model is not None:
        _validate_against_schema(output, policy.schema_model)
    if policy.assertion_callback is not None:
        verdict = policy.assertion_callback(output)
        if inspect.isawaitable(verdict):
            verdict = await verdict
        if not verdict:
            raise _OutputAssertionError(
                "Output validation assertion_callback returned False."
            )


def _compute_retry_delay(
    tool_name: str, attempt: int, err: BaseException, policy: _ValidationPolicy
) -> float:
    span = trace.get_current_span()
    if span.is_recording():
        span.add_event(
            "tool.validation.retry",
            attributes={
                "tool.name": tool_name,
                "attempt": attempt,
                "max_retries": policy.total_attempts,
                "error": validation.scrub_sensitive_text(str(err)),
            },
        )
    base = policy.retry_delay_seconds * (policy.backoff_factor ** (attempt - 1))
    jitter = (
        random.uniform(0.0, policy.jitter_seconds)  # noqa: S311
        if policy.jitter_seconds > 0.0
        else 0.0
    )
    return max(0.0, base + jitter)


def _stamp_span(
    tool_name: str,
    status: str,
    attempt: int,
    policy: _ValidationPolicy,
    err: BaseException | None = None,
) -> None:
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("tool.validation.tool_name", tool_name)
        if policy.schema_model is not None:
            span.set_attribute(
                "tool.validation.schema", policy.schema_model.__name__
            )
        span.set_attribute("tool.validation.status", status)
        span.set_attribute("tool.validation.attempts", attempt)
        if err is not None:
            span.set_attribute(
                "tool.validation.error",
                validation.scrub_sensitive_text(str(err)),
            )


def _raise_exhausted(
    tool_name: str, err: BaseException, policy: _ValidationPolicy
) -> NoReturn:
    _stamp_span(tool_name, "FAIL", policy.total_attempts, policy, err)
    raise exceptions.ToolValidationError(
        f"Tool {tool_name!r} output validation failed after "
        f"{policy.total_attempts} attempt(s): {err}",
        tool_name=tool_name,
        attempts=policy.total_attempts,
        last_error=err,
    ) from err


def validate_tool_output(
    *,
    schema_model: type[pydantic.BaseModel] | None = None,
    assertion_callback: Callable[[Any], bool | Awaitable[bool]] | None = None,
    max_retries: int = 3,
    retry_delay_seconds: float = 0.0,
    backoff_factor: float = 2.0,
    jitter_seconds: float = 0.0,
    retry_on_exceptions: tuple[type[BaseException], ...] = (),
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Validates tool output via Pydantic schema and assertion callback."""
    policy = _ValidationPolicy(
        schema_model=schema_model,
        assertion_callback=assertion_callback,
        total_attempts=max(1, max_retries),
        retry_delay_seconds=retry_delay_seconds,
        backoff_factor=backoff_factor,
        jitter_seconds=jitter_seconds,
        retry_on_exceptions=retry_on_exceptions,
    )

    def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
        if inspect.iscoroutinefunction(func):
            return _wrap_async_tool(func, policy)
        return _wrap_sync_tool(func, policy)

    return decorator


def _wrap_sync_tool(
    func: Callable[_P, _R], policy: _ValidationPolicy
) -> Callable[_P, _R]:
    tool_name = func.__name__

    @functools.wraps(func)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        last_err: BaseException = RuntimeError("Validation failed.")
        for attempt in range(1, policy.total_attempts + 1):
            try:
                result = func(*args, **kwargs)
            except BaseException as tool_exc:
                if policy.retry_on_exceptions and isinstance(
                    tool_exc, policy.retry_on_exceptions
                ):
                    last_err = tool_exc
                else:
                    raise
            else:
                try:
                    _check_sync_output(result, policy)
                    _stamp_span(tool_name, "PASS", attempt, policy)
                    return result
                except Exception as val_exc:  # noqa: BLE001
                    last_err = val_exc
            if attempt < policy.total_attempts:
                delay = _compute_retry_delay(
                    tool_name, attempt, last_err, policy
                )
                if delay > 0.0:
                    time.sleep(delay)
        _raise_exhausted(tool_name, last_err, policy)

    return wrapper


def _wrap_async_tool(
    func: Callable[_P, Any], policy: _ValidationPolicy
) -> Callable[_P, Any]:
    tool_name = func.__name__

    @functools.wraps(func)
    async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> Any:
        last_err: BaseException = RuntimeError("Validation failed.")
        for attempt in range(1, policy.total_attempts + 1):
            try:
                result = await func(*args, **kwargs)
            except BaseException as tool_exc:
                if policy.retry_on_exceptions and isinstance(
                    tool_exc, policy.retry_on_exceptions
                ):
                    last_err = tool_exc
                else:
                    raise
            else:
                try:
                    await _check_async_output(result, policy)
                    _stamp_span(tool_name, "PASS", attempt, policy)
                    return result
                except Exception as val_exc:  # noqa: BLE001
                    last_err = val_exc
            if attempt < policy.total_attempts:
                delay = _compute_retry_delay(
                    tool_name, attempt, last_err, policy
                )
                if delay > 0.0:
                    await asyncio.sleep(delay)
        _raise_exhausted(tool_name, last_err, policy)

    return wrapper
