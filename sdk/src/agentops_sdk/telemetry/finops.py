# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""FinOps token telemetry, sensitive log scrubbing, and usage extraction."""

from __future__ import annotations

from collections.abc import Mapping
import contextlib
import logging
import threading
from typing import Any

from google.cloud import logging as cloud_logging
from opentelemetry import trace

from agentops_sdk.config import settings
from agentops_sdk.domain import context as domain_context
from agentops_sdk.domain import models
from agentops_sdk.domain import validation
from agentops_sdk.telemetry import _sink_helpers
from agentops_sdk.telemetry import provider

logger = logging.getLogger(__name__)
_FINOPS_CLIENT_LOCK = threading.Lock()
_FINOPS_CLOUD_CLIENT: dict[str | None, Any] | None = {}
validate_local_sink_path = _sink_helpers.validate_local_sink_path
scrub_sensitive_text = validation.scrub_sensitive_text


def reset_finops_clients() -> None:
    """Closes and clears cached FinOps Cloud Logging clients."""
    global _FINOPS_CLOUD_CLIENT
    with _FINOPS_CLIENT_LOCK:
        clients = (
            list(_FINOPS_CLOUD_CLIENT.values())
            if isinstance(_FINOPS_CLOUD_CLIENT, dict)
            else []
        )
        _FINOPS_CLOUD_CLIENT = {}
    for cli in clients:
        close_fn = getattr(cli, "close", None)
        if callable(close_fn):
            with contextlib.suppress(Exception):
                close_fn()


def resolve_telemetry_context(
    explicit: models.TelemetryContext | None,
    kpi_id: str | None = None,
    pl_line: str | None = None,
    project: str | None = None,
    env: str | None = None,
) -> models.TelemetryContext:
    """Merges explicit/ambient TelemetryContext with decorator overrides."""
    base = (
        explicit
        or domain_context.current_execution_scope().telemetry_context
        or models.TelemetryContext()
    )
    return models.TelemetryContext(
        business_kpi=kpi_id or base.business_kpi,
        pl_impact_line=pl_line or base.pl_impact_line,
        project_name=project or base.project_name,
        environment=env or base.environment,
        session_id=base.session_id,
        user_id=base.user_id,
    )


def build_scope_span_attrs(
    tel_ctx: models.TelemetryContext, role: str | None = None
) -> dict[str, str]:
    """Builds standard FinOps and project OTel span attributes."""
    attrs = {
        "business.kpi_id": tel_ctx.business_kpi,
        "business.pl_impact_line": tel_ctx.pl_impact_line,
        "agentops.project.name": tel_ctx.project_name,
        "agentops.environment": tel_ctx.environment,
    }
    if role:
        attrs["agentops.agent.role"] = role
        attrs["agentops.agent.name"] = role
    return attrs


def log_llm_metrics(
    *,
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
) -> None:
    """Attaches LLM and GenAI semantic convention attributes to active span."""
    span = trace.get_current_span()
    if span is not None and span.is_recording():
        span.set_attribute("llm.model_name", model_name)
        span.set_attribute("llm.usage.prompt_tokens", int(input_tokens))
        span.set_attribute("llm.usage.completion_tokens", int(output_tokens))
        span.set_attribute("llm.usage.cached_tokens", int(cached_tokens))
        span.set_attribute("gen_ai.request.model", model_name)
        span.set_attribute("gen_ai.usage.input_tokens", int(input_tokens))
        span.set_attribute("gen_ai.usage.output_tokens", int(output_tokens))
    tracker = provider.get_meter_tracker()
    tracker.add_counter(
        "gen_ai.client.token.usage",
        int(input_tokens),
        {"gen_ai.token.type": "input", "gen_ai.request.model": model_name},
    )
    tracker.add_counter(
        "gen_ai.client.token.usage",
        int(output_tokens),
        {"gen_ai.token.type": "output", "gen_ai.request.model": model_name},
    )


def _lookup_int(source: Any, keys: tuple[str, ...]) -> int:
    for key in keys if source is not None else ():
        val = (
            source.get(key)
            if isinstance(source, Mapping)
            else getattr(source, key, None)
        )
        if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
            return val
    return 0


def _lookup_str(source: Any, keys: tuple[str, ...]) -> str | None:
    for key in keys if source is not None else ():
        val = (
            source.get(key)
            if isinstance(source, Mapping)
            else getattr(source, key, None)
        )
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def extract_llm_usage(
    response: Any,
    *,
    model_name_override: str | None = None,
    latency_ms: float = 0.0,
) -> models.LLMUsageMetric:
    """Extracts token counts and model metadata from dict or SDK responses."""
    if isinstance(response, models.LLMUsageMetric):
        return response.model_copy(
            update={
                "model_name": model_name_override or response.model_name,
                "latency_ms": round(latency_ms or response.latency_ms, 3),
            }
        )
    usage = (
        response.get("usage_metadata") or response.get("usage")
        if isinstance(response, Mapping)
        else getattr(response, "usage_metadata", None)
        or getattr(response, "usage", None)
    )
    detected = _lookup_str(
        response, ("model_version", "model_name", "model")
    ) or _lookup_str(usage, ("model_name", "model"))
    in_k = ("prompt_token_count", "input_tokens", "prompt_tokens")
    out_k = ("candidates_token_count", "output_tokens", "completion_tokens")
    c_k = ("cached_content_token_count", "cached_tokens")
    return models.LLMUsageMetric(
        model_name=model_name_override or detected or "unknown",
        input_tokens=_lookup_int(usage, in_k) or _lookup_int(response, in_k),
        output_tokens=_lookup_int(usage, out_k) or _lookup_int(response, out_k),
        cached_tokens=_lookup_int(usage, c_k) or _lookup_int(response, c_k),
        latency_ms=round(latency_ms, 3),
        status="OK",
    )


def _write_gcp_finops_log(payload: dict[str, Any]) -> None:
    global _FINOPS_CLOUD_CLIENT
    try:
        proj, _ = settings.resolve_gcp_project_and_location()
        with _FINOPS_CLIENT_LOCK:
            if not isinstance(_FINOPS_CLOUD_CLIENT, dict):
                _FINOPS_CLOUD_CLIENT = {}
            client = _FINOPS_CLOUD_CLIENT.get(proj)
            if client is None:
                cls: Any = cloud_logging.Client
                client = cls(project=proj)
                _FINOPS_CLOUD_CLIENT[proj] = client
        provider.register_reset_hook(reset_finops_clients)
        log_name = settings.get_telemetry_settings().finops_log_name
        severity = "INFO" if payload.get("llm.status") == "OK" else "ERROR"
        client.logger(log_name).log_struct(payload, severity=severity)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cloud Logging FinOps export skipped: %s", exc)
        provider.record_fallback_metric("finops_logger", "cloud_logging_error")


def _build_finops_payload(
    usage: models.LLMUsageMetric,
    telemetry_context: models.TelemetryContext,
    scope: domain_context.ExecutionScope,
    span: trace.Span | None,
) -> dict[str, Any]:
    active_span = span if span is not None else trace.get_current_span()
    s_ctx = active_span.get_span_context() if active_span else None
    t_hex = f"{s_ctx.trace_id:032x}" if s_ctx and s_ctx.is_valid else ""
    sp_hex = f"{s_ctx.span_id:016x}" if s_ctx and s_ctx.is_valid else ""
    payload: dict[str, Any] = {
        "event_type": "finops_llm_telemetry",
        "llm.model_name": usage.model_name,
        "llm.input_tokens": usage.input_tokens,
        "llm.output_tokens": usage.output_tokens,
        "llm.cached_tokens": usage.cached_tokens,
        "llm.latency_ms": usage.latency_ms,
        "llm.status": usage.status,
        "business.kpi_id": telemetry_context.business_kpi,
        "business.pl_impact_line": telemetry_context.pl_impact_line,
        "agentops.project.name": telemetry_context.project_name,
        "agentops.environment": telemetry_context.environment,
        "agentops.session.id": scope.session_id,
        "agentops.user.id": scope.user_id,
        "trace_id": t_hex,
        "span_id": sp_hex,
        "timestamp": models.utc_now_iso(),
    }
    if usage.error_message:
        payload["error_message"] = scrub_sensitive_text(usage.error_message)
    return payload


def emit_finops_telemetry(
    usage: models.LLMUsageMetric,
    *,
    telemetry_context: models.TelemetryContext,
    scope: domain_context.ExecutionScope,
    span: trace.Span | None = None,
) -> dict[str, Any]:
    """Emits a structured FinOps LLM telemetry event."""
    payload = _build_finops_payload(usage, telemetry_context, scope, span)
    logger.info(
        "FinOps LLM telemetry model=%s status=%s",
        usage.model_name,
        usage.status,
        extra={"json_fields": payload},
    )
    if provider.get_active_telemetry_mode() in (
        models.TelemetryMode.GCP,
        models.TelemetryMode.HYBRID,
    ):
        _write_gcp_finops_log(payload)
    return payload
