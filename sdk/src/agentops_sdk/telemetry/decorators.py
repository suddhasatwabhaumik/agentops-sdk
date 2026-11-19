# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""OpenTelemetry tracing and FinOps attribution decorators for agents."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import contextlib
import dataclasses
import functools
import inspect
import time
from typing import Any, ParamSpec, TypeVar

from opentelemetry import trace

from agentops_sdk.domain import context as domain_context
from agentops_sdk.domain import lifecycle
from agentops_sdk.domain import models
from agentops_sdk.domain import validation
from agentops_sdk.telemetry import finops

_P = ParamSpec("_P")
_R = TypeVar("_R")
_TRACER_NAME = "agentops_sdk"


@dataclasses.dataclass
class _SpanCallState:
    """Per-invocation context stack, span, and timing metadata."""

    stack: contextlib.ExitStack
    span: trace.Span
    scope: domain_context.ExecutionScope
    telemetry_context: models.TelemetryContext
    started_at: float


def _open_span(
    span_name: str,
    stack: contextlib.ExitStack,
    scope: domain_context.ExecutionScope,
    tel_ctx: models.TelemetryContext,
    attrs: Mapping[str, str],
) -> _SpanCallState:
    span = stack.enter_context(
        trace.get_tracer(_TRACER_NAME).start_as_current_span(span_name)
    )
    if span.is_recording():
        span.set_attribute("agentops.session.id", scope.session_id)
        span.set_attribute("agentops.user.id", scope.user_id)
        for key, val in attrs.items():
            span.set_attribute(key, val)
    return _SpanCallState(stack, span, scope, tel_ctx, time.perf_counter())


def _mark_ok(state: _SpanCallState, result: Any) -> Any:
    state.span.set_status(trace.Status(trace.StatusCode.OK))
    return result


def record_scrubbed_exception(
    span: trace.Span | None, exc: BaseException | None
) -> str | None:
    """Scrubs exc string, records span exception, and sets error status."""
    if exc is None:
        return None
    scrubbed = validation.scrub_sensitive_text(str(exc))
    if span is not None:
        exc_type = f"{type(exc).__module__}.{type(exc).__qualname__}"
        attrs = {"exception.type": exc_type, "exception.message": scrubbed}
        span.record_exception(
            RuntimeError(scrubbed or "error"), attributes=attrs
        )
        span.set_status(trace.Status(trace.StatusCode.ERROR, scrubbed or "err"))
    return scrubbed


def _mark_error(state: _SpanCallState, exc: BaseException) -> None:
    record_scrubbed_exception(state.span, exc)


def _close_state(state: _SpanCallState) -> None:
    state.stack.close()


def _enter_flow(
    span_title: str,
    agent_role: str | None,
    tel_ctx: models.TelemetryContext,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[_SpanCallState, tuple[Any, ...], dict[str, Any]]:
    verified = bool(
        domain_context.identity_claims_ctx.get()
        or domain_context.user_id_ctx.get()
    )
    kw_s, kw_u = kwargs.get("session_id"), kwargs.get("user_id")
    uid = kw_u if isinstance(kw_u, str) else tel_ctx.user_id
    eff_uid = None if verified else uid
    eff_tel = (
        tel_ctx.model_copy(update={"user_id": None})
        if verified and tel_ctx.user_id
        else tel_ctx
    )
    sid = kw_s if isinstance(kw_s, str) else tel_ctx.session_id
    stack = contextlib.ExitStack()
    scope = stack.enter_context(
        domain_context.bind_execution_scope(
            session_id=sid, user_id=eff_uid, telemetry_context=eff_tel
        )
    )
    attrs = finops.build_scope_span_attrs(eff_tel, agent_role)
    return _open_span(span_title, stack, scope, eff_tel, attrs), args, kwargs


def _enter_agent(
    span_title: str,
    attrs: Mapping[str, str],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[_SpanCallState, tuple[Any, ...], dict[str, Any]]:
    scope = domain_context.current_execution_scope()
    tel_ctx = scope.telemetry_context or models.TelemetryContext()
    st = _open_span(span_title, contextlib.ExitStack(), scope, tel_ctx, attrs)
    return st, args, kwargs


def _enter_llm(
    span_title: str,
    tel_ctx: models.TelemetryContext,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[_SpanCallState, tuple[Any, ...], dict[str, Any]]:
    scope = domain_context.current_execution_scope()
    attrs = finops.build_scope_span_attrs(tel_ctx)
    st = _open_span(span_title, contextlib.ExitStack(), scope, tel_ctx, attrs)
    return st, args, kwargs


def _finish_llm_ok(
    state: _SpanCallState, result: Any, model_override: str | None
) -> Any:
    ms = (time.perf_counter() - state.started_at) * 1000.0
    usage = finops.extract_llm_usage(
        result, model_name_override=model_override, latency_ms=ms
    )
    finops.log_llm_metrics(
        model_name=usage.model_name,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=usage.cached_tokens,
    )
    state.span.set_status(trace.Status(trace.StatusCode.OK))
    finops.emit_finops_telemetry(
        usage,
        telemetry_context=state.telemetry_context,
        scope=state.scope,
        span=state.span,
    )
    return result


def _finish_llm_err(
    state: _SpanCallState, exc: BaseException, model_override: str | None
) -> None:
    ms = round((time.perf_counter() - state.started_at) * 1000.0, 3)
    err_msg = validation.scrub_sensitive_text(str(exc))
    usage = models.LLMUsageMetric(
        model_name=model_override or "unknown",
        latency_ms=ms,
        status="ERROR",
        error_message=err_msg,
    )
    finops.log_llm_metrics(
        model_name=usage.model_name, input_tokens=0, output_tokens=0
    )
    _mark_error(state, exc)
    finops.emit_finops_telemetry(
        usage,
        telemetry_context=state.telemetry_context,
        scope=state.scope,
        span=state.span,
    )


def trace_agent_flow(
    context: models.TelemetryContext | None = None,
    *,
    flow_name: str | None = None,
    agent_role: str | None = None,
    kpi_id: str | None = None,
    pl_impact_line: str | None = None,
    project_name: str | None = None,
    environment: str | None = None,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Binds ambient execution scope and starts an agent_flow OTel span."""

    def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
        title = f"agent_flow:{flow_name or func.__name__}"
        ctx_args = (context, kpi_id, pl_impact_line, project_name, environment)
        return lifecycle.bind_sync_or_async(
            func,
            on_enter=lambda a, kw: _enter_flow(
                title,
                agent_role,
                finops.resolve_telemetry_context(*ctx_args),
                a,
                kw,
            ),
            on_success=_mark_ok,
            on_error=_mark_error,
            on_finally=_close_state,
        )

    return decorator


def trace_agent_span(
    span_name: str | None = None,
    *,
    agent_name: str | None = None,
    agent_urn: str | None = None,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Starts an agent span tagged with agentops.agent.name and agent.urn."""

    def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
        r_agent = agent_name or span_name or func.__name__
        attrs = {
            "agentops.agent.name": r_agent,
            "agentops.agent.urn": agent_urn or f"urn:agentops:agent:{r_agent}",
        }
        name = (
            f"agent_run:{agent_name}"
            if agent_name
            else (span_name or func.__name__)
        )
        return lifecycle.bind_sync_or_async(
            func,
            on_enter=lambda a, kw: _enter_agent(name, attrs, a, kw),
            on_success=_mark_ok,
            on_error=_mark_error,
            on_finally=_close_state,
        )

    return decorator


def trace_llm_call(
    context: models.TelemetryContext | None = None,
    *,
    model_name_override: str | None = None,
    kpi_id: str | None = None,
    pl_impact_line: str | None = None,
    project_name: str | None = None,
    environment: str | None = None,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Traces an LLM invocation, extracts token usage, and emits FinOps logs."""

    def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
        title = f"llm_call:{func.__name__}"
        ctx_args = (context, kpi_id, pl_impact_line, project_name, environment)
        return lifecycle.bind_sync_or_async(
            func,
            on_enter=lambda a, kw: _enter_llm(
                title, finops.resolve_telemetry_context(*ctx_args), a, kw
            ),
            on_success=lambda s, r: _finish_llm_ok(s, r, model_name_override),
            on_error=lambda s, e: _finish_llm_err(s, e, model_name_override),
            on_finally=_close_state,
        )

    return decorator


def wrap_context_manager(
    func: Callable[_P, _R], factory: Callable[[], Any]
) -> Callable[_P, _R]:
    """Wraps a sync or async callable inside a sync/async context manager."""
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_w(*args: _P.args, **kwargs: _P.kwargs) -> Any:
            async with factory():
                return await func(*args, **kwargs)

        return async_w  # type: ignore[return-value]

    @functools.wraps(func)
    def sync_w(*args: _P.args, **kwargs: _P.kwargs) -> Any:
        with factory():
            return func(*args, **kwargs)

    return sync_w
