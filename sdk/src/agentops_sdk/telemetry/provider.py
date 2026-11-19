# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unified OpenTelemetry provider configuration and instrument caching."""

from __future__ import annotations

from collections.abc import Callable
import contextlib
import dataclasses
import logging
import os
import sys
import threading
from typing import Any

from opentelemetry import _logs
from opentelemetry import metrics
from opentelemetry import trace
from opentelemetry._logs import _internal as logs_internal
from opentelemetry.metrics import _internal as metrics_internal
from opentelemetry.sdk import metrics as sdk_metrics
from opentelemetry.sdk import resources as sdk_resources
from opentelemetry.sdk import trace as sdk_trace
from opentelemetry.util import _once

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import models
from agentops_sdk.telemetry import _meters
from agentops_sdk.telemetry import gcp_exporters
from agentops_sdk.telemetry import otlp_exporters

logger = logging.getLogger(__name__)
_PROVIDER_LOCK = threading.RLock()
_ACTIVE_HANDLES: TelemetryHandles | None = None
_RESET_HOOKS: list[Callable[[], None]] = []

CachedMeterTracker = _meters.CachedMeterTracker
_METER_TRACKER = _meters._METER_TRACKER
get_meter_tracker = _meters.get_meter_tracker
record_fallback_metric = _meters.record_fallback_metric


def register_reset_hook(hook: Callable[[], None]) -> None:
    """Registers a one-shot cleanup callback cleared on reset_telemetry()."""
    with _PROVIDER_LOCK:
        if hook not in _RESET_HOOKS:
            _RESET_HOOKS.append(hook)


@dataclasses.dataclass
class TelemetryHandles:
    """Active OpenTelemetry provider handles and flush/shutdown controls."""

    mode: models.TelemetryMode
    tracer_provider: Any = None
    meter_provider: Any = None
    logger_provider: Any = None
    _log_handler: logging.Handler | None = dataclasses.field(
        default=None, repr=False
    )

    def flush(self) -> None:
        """Flushes pending spans, metrics, and log records."""
        for prov in (
            self.tracer_provider,
            self.meter_provider,
            self.logger_provider,
        ):
            if prov is not None:
                with contextlib.suppress(Exception):
                    prov.force_flush()

    def shutdown(self) -> None:
        """Detaches handlers and shuts down active OpenTelemetry providers."""
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None
        for prov in (
            self.tracer_provider,
            self.meter_provider,
            self.logger_provider,
        ):
            if prov is not None:
                with contextlib.suppress(Exception):
                    prov.shutdown()


def resolve_telemetry_mode(
    mode: models.TelemetryMode | str = models.TelemetryMode.AUTO,
    *,
    project_id: str | None = None,
    collector_url: str | None = None,
) -> models.TelemetryMode:
    """Resolves AUTO mode into OTLP, GCP, or OFF."""
    try:
        parsed = (
            mode
            if isinstance(mode, models.TelemetryMode)
            else models.TelemetryMode(mode.strip().lower())
        )
    except ValueError as exc:
        raise exceptions.ConfigurationError(f"Invalid mode: {mode!r}") from exc
    cfg = settings.get_telemetry_settings()
    if (
        parsed == models.TelemetryMode.AUTO
        and cfg.mode != models.TelemetryMode.AUTO
    ):
        parsed = cfg.mode
    if parsed != models.TelemetryMode.AUTO:
        return parsed
    if collector_url or cfg.collector_url:
        return models.TelemetryMode.OTLP
    if settings.resolve_gcp_project_and_location(project_id)[0] is not None:
        return models.TelemetryMode.GCP
    return models.TelemetryMode.OFF


def _build_resource(service_name: str | None) -> sdk_resources.Resource:
    env_res = sdk_resources.Resource.create()
    env_svc = env_res.attributes.get("service.name")
    svc = service_name or os.getenv("OTEL_SERVICE_NAME")
    if not svc:
        if isinstance(env_svc, str) and not env_svc.startswith(
            "unknown_service"
        ):
            svc = env_svc
        else:
            svc = settings.get_telemetry_settings().service_name
    sdk_res = sdk_resources.Resource.create(
        {"service.name": svc, "telemetry.sdk.name": "agentops-sdk"}
    )
    return env_res.merge(sdk_res)


def _reset_loaded_module_caches() -> None:
    mod_targets = (
        ("agentops_sdk.telemetry._sink_helpers", "reset_cloud_logging_clients"),
        ("agentops_sdk.telemetry.finops", "reset_finops_clients"),
        ("agentops_sdk.telemetry.case_tracker", "reset_default_exporter"),
    )
    for mod_name, fn_name in mod_targets:
        mod = sys.modules.get(mod_name)
        fn = getattr(mod, fn_name, None) if mod is not None else None
        if callable(fn):
            with contextlib.suppress(Exception):
                fn()


def reset_telemetry() -> None:
    """Shuts down active telemetry providers and resets global state."""
    global _ACTIVE_HANDLES
    with _PROVIDER_LOCK:
        if _ACTIVE_HANDLES is not None:
            _ACTIVE_HANDLES.shutdown()
            _ACTIVE_HANDLES = None
        otlp_exporters.set_active_mode(None)
        trace._TRACER_PROVIDER = None
        trace._TRACER_PROVIDER_SET_ONCE = _once.Once()
        metrics_internal._METER_PROVIDER = None
        metrics_internal._METER_PROVIDER_SET_ONCE = _once.Once()
        metrics_internal._PROXY_METER_PROVIDER = (
            metrics_internal._ProxyMeterProvider()
        )
        logs_internal._LOGGER_PROVIDER = None
        logs_internal._LOGGER_PROVIDER_SET_ONCE = _once.Once()
        _METER_TRACKER.clear()
        hooks = list(_RESET_HOOKS)
        _RESET_HOOKS.clear()
    for hook in hooks:
        with contextlib.suppress(Exception):
            hook()
    _reset_loaded_module_caches()


def get_active_telemetry_mode() -> models.TelemetryMode:
    """Returns the currently configured TelemetryMode."""
    return otlp_exporters.resolve_active_mode()


def _rollback_providers(
    handler: logging.Handler | None,
    provs: tuple[Any, ...],
    readers: list[Any],
) -> None:
    if handler is not None:
        logging.getLogger().removeHandler(handler)
    for prov in provs:
        if prov is not None:
            with contextlib.suppress(Exception):
                prov.shutdown()
    for reader in readers:
        with contextlib.suppress(Exception):
            reader.shutdown()


def _install_providers(
    r_mode: models.TelemetryMode,
    resource: sdk_resources.Resource,
    project_id: str | None,
    collector_url: str | None,
) -> TelemetryHandles:
    t_prov: sdk_trace.TracerProvider | None = None
    m_prov: sdk_metrics.MeterProvider | None = None
    l_prov: Any = None
    handler: logging.Handler | None = None
    readers: list[Any] = []
    try:
        t_prov = sdk_trace.TracerProvider(resource=resource)
        if r_mode in (models.TelemetryMode.OTLP, models.TelemetryMode.HYBRID):
            t_prov.add_span_processor(
                otlp_exporters.create_otlp_span_processor(collector_url)
            )
            readers.append(
                otlp_exporters.create_otlp_metric_reader(collector_url)
            )
            l_prov, handler = otlp_exporters.configure_otlp_logging(
                resource, collector_url
            )
        if r_mode in (models.TelemetryMode.GCP, models.TelemetryMode.HYBRID):
            t_prov.add_span_processor(
                gcp_exporters.create_gcp_span_processor(project_id)
            )
            readers.append(gcp_exporters.create_gcp_metric_reader(project_id))
        m_prov = sdk_metrics.MeterProvider(
            resource=resource, metric_readers=readers
        )
        if l_prov is not None:
            _logs.set_logger_provider(l_prov)
        trace.set_tracer_provider(t_prov)
        metrics.set_meter_provider(m_prov)
        return TelemetryHandles(r_mode, t_prov, m_prov, l_prov, handler)
    except Exception:
        _rollback_providers(handler, (t_prov, m_prov, l_prov), readers)
        raise


def configure_telemetry(
    *,
    mode: models.TelemetryMode | str = models.TelemetryMode.AUTO,
    project_id: str | None = None,
    service_name: str | None = None,
    collector_url: str | None = None,
) -> TelemetryHandles:
    """Configures OpenTelemetry trace, metric, and log providers."""
    global _ACTIVE_HANDLES
    r_mode = resolve_telemetry_mode(
        mode, project_id=project_id, collector_url=collector_url
    )
    r_url = collector_url or settings.get_telemetry_settings().collector_url
    resource = _build_resource(service_name)
    with _PROVIDER_LOCK:
        reset_telemetry()
        if r_mode == models.TelemetryMode.OFF:
            otlp_exporters.set_active_mode(r_mode)
            _ACTIVE_HANDLES = TelemetryHandles(mode=models.TelemetryMode.OFF)
            return _ACTIVE_HANDLES
        handles = _install_providers(r_mode, resource, project_id, r_url)
        otlp_exporters.set_active_mode(r_mode)
        _ACTIVE_HANDLES = handles
        logger.info("Configured AgentOps telemetry in %s mode.", r_mode)
        return _ACTIVE_HANDLES
