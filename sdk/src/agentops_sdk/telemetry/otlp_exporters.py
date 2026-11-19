# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Vendor-neutral OpenTelemetry OTLP span, metric, and log exporters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import logging
import os
import threading
import time
from typing import Any, TypeVar
from urllib import parse as urllib_parse

from opentelemetry import _logs
from opentelemetry import context as otel_context
from opentelemetry.exporter.otlp.proto.grpc import (
    _log_exporter as grpc_log_exporter,
)
from opentelemetry.exporter.otlp.proto.grpc import (
    metric_exporter as grpc_metric_exporter,
)
from opentelemetry.exporter.otlp.proto.grpc import (
    trace_exporter as grpc_trace_exporter,
)
from opentelemetry.exporter.otlp.proto.http import (
    _log_exporter as http_log_exporter,
)
from opentelemetry.exporter.otlp.proto.http import (
    metric_exporter as http_metric_exporter,
)
from opentelemetry.exporter.otlp.proto.http import (
    trace_exporter as http_trace_exporter,
)
from opentelemetry.sdk import _logs as sdk_logs
from opentelemetry.sdk import resources as sdk_resources
from opentelemetry.sdk._logs import export as sdk_log_export
from opentelemetry.sdk.metrics import export as sdk_metric_export
from opentelemetry.sdk.trace import export as sdk_trace_export
from typing_extensions import override

from agentops_sdk.config import settings
from agentops_sdk.domain import models
from agentops_sdk.domain import validation

_DEFAULT_OTLP_ENDPOINT = "http://localhost:4317"
_SIGNAL_SUFFIXES = ("/v1/traces", "/v1/metrics", "/v1/logs")
_SEVERITY_TEXT_MAP = {"WARNING": "WARN", "CRITICAL": "FATAL"}
_ACTIVE_MODE_LOCK = threading.Lock()
_LOGGING_LOCK = threading.Lock()
_ACTIVE_MODE: models.TelemetryMode | None = None

scrub_text = validation.scrub_sensitive_text


def set_active_mode(mode: models.TelemetryMode | None) -> None:
    """Updates the active telemetry routing mode."""
    global _ACTIVE_MODE
    with _ACTIVE_MODE_LOCK:
        _ACTIVE_MODE = mode


def resolve_active_mode() -> models.TelemetryMode:
    """Returns the currently active TelemetryMode."""
    with _ACTIVE_MODE_LOCK:
        if _ACTIVE_MODE is not None:
            return _ACTIVE_MODE
    cfg = settings.get_telemetry_settings()
    if cfg.mode != models.TelemetryMode.AUTO:
        return cfg.mode
    if cfg.collector_url:
        return models.TelemetryMode.OTLP
    if settings.resolve_gcp_project_and_location()[0] is not None:
        return models.TelemetryMode.GCP
    return models.TelemetryMode.OFF


def parse_otlp_endpoint(collector_url: str | None) -> tuple[str, bool, bool]:
    """Parses collector URL into (normalized_url, insecure, use_http)."""
    raw = (collector_url or "").strip() or _DEFAULT_OTLP_ENDPOINT
    normalized = raw if "://" in raw else f"http://{raw}"
    parsed_url = urllib_parse.urlparse(normalized)
    insecure = parsed_url.scheme == "http"
    proto_env = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "").strip().lower()
    path = parsed_url.path.rstrip("/")
    use_http = (
        proto_env.startswith("http")
        or parsed_url.port == 4318
        or "/v1/" in parsed_url.path
        or path.endswith("/v1")
    )
    return normalized, insecure, use_http


def resolve_http_signal_endpoint(base_url: str, signal: str) -> str:
    """Builds the per-signal OTLP/HTTP endpoint URL."""
    trimmed = base_url.rstrip("/")
    for suffix in _SIGNAL_SUFFIXES:
        if trimmed.endswith(suffix):
            trimmed = trimmed[: -len(suffix)]
            break
    return (
        f"{trimmed}/{signal}"
        if trimmed.endswith("/v1")
        else f"{trimmed}/v1/{signal}"
    )


def _map_severity(levelno: int) -> _logs.SeverityNumber:
    if levelno >= logging.CRITICAL:
        return _logs.SeverityNumber.FATAL
    if levelno >= logging.ERROR:
        return _logs.SeverityNumber.ERROR
    if levelno >= logging.WARNING:
        return _logs.SeverityNumber.WARN
    if levelno >= logging.INFO:
        return _logs.SeverityNumber.INFO
    return _logs.SeverityNumber.DEBUG


def _flatten_json_fields(
    payload: Mapping[str, Any],
    target: dict[str, str | int | float | bool],
    prefix: str = "",
    depth: int = 0,
) -> None:
    for raw_key, val in payload.items():
        if val is None:
            continue
        key = f"{prefix}.{raw_key}" if prefix else str(raw_key)
        if isinstance(val, bool | int | float | str):
            target[key] = val
        elif isinstance(val, Mapping) and depth < 2:
            _flatten_json_fields(val, target, prefix=key, depth=depth + 1)
        else:
            target[key] = json.dumps(val, default=str)


class OtlpStdlibLogHandler(logging.Handler):
    """Bridges agentops stdlib records into OpenTelemetry OTLP logs."""

    def __init__(
        self,
        logger_provider: sdk_logs.LoggerProvider,
        level: int = logging.INFO,
    ) -> None:
        super().__init__(level=level)
        self._logger_provider = logger_provider

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        """Restricts OTLP forwarding to agentops_sdk and agentops.* loggers."""
        return record.name.startswith(("agentops_sdk", "agentops."))

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Translates a matching stdlib LogRecord into an OTLP LogRecord."""
        if not self.filter(record):
            return
        attrs: dict[str, str | int | float | bool] = {
            "logger.name": record.name,
            "code.function": record.funcName,
            "code.lineno": record.lineno,
        }
        json_fields = getattr(record, "json_fields", None)
        if isinstance(json_fields, Mapping):
            _flatten_json_fields(json_fields, attrs)
        otel_logger = self._logger_provider.get_logger(record.name)
        if isinstance(otel_logger, _logs.NoOpLogger):
            return
        sev_text = _SEVERITY_TEXT_MAP.get(record.levelname, record.levelname)
        otel_logger.emit(
            _logs.LogRecord(
                timestamp=int(record.created * 1_000_000_000),
                observed_timestamp=time.time_ns(),
                context=otel_context.get_current(),
                severity_text=sev_text,
                severity_number=_map_severity(record.levelno),
                body=record.getMessage(),
                attributes=attrs,
            )
        )


_ExpT = TypeVar("_ExpT")


def _build_otlp_exporter(
    collector_url: str | None,
    signal: str,
    http_cls: Callable[[str], _ExpT],
    grpc_cls: Callable[..., _ExpT],
) -> _ExpT:
    url, insecure, use_http = parse_otlp_endpoint(collector_url)
    if use_http:
        return http_cls(resolve_http_signal_endpoint(url, signal))
    return grpc_cls(endpoint=url, insecure=insecure)


def create_otlp_span_processor(
    collector_url: str | None = None,
) -> sdk_trace_export.BatchSpanProcessor:
    """Creates an OTLP gRPC or HTTP BatchSpanProcessor."""
    exp = _build_otlp_exporter(
        collector_url,
        "traces",
        http_trace_exporter.OTLPSpanExporter,
        grpc_trace_exporter.OTLPSpanExporter,
    )
    return sdk_trace_export.BatchSpanProcessor(exp)


def create_otlp_metric_reader(
    collector_url: str | None = None, export_interval_millis: float = 60000.0
) -> sdk_metric_export.PeriodicExportingMetricReader:
    """Creates an OTLP gRPC or HTTP PeriodicExportingMetricReader."""
    exp = _build_otlp_exporter(
        collector_url,
        "metrics",
        http_metric_exporter.OTLPMetricExporter,
        grpc_metric_exporter.OTLPMetricExporter,
    )
    return sdk_metric_export.PeriodicExportingMetricReader(
        exp, export_interval_millis=export_interval_millis
    )


def configure_otlp_logging(
    resource: sdk_resources.Resource, collector_url: str | None = None
) -> tuple[sdk_logs.LoggerProvider, OtlpStdlibLogHandler]:
    """Configures OTLP LoggerProvider and attaches OtlpStdlibLogHandler."""
    exp = _build_otlp_exporter(
        collector_url,
        "logs",
        http_log_exporter.OTLPLogExporter,
        grpc_log_exporter.OTLPLogExporter,
    )
    logger_prov = sdk_logs.LoggerProvider(
        resource=resource, shutdown_on_exit=False
    )
    logger_prov.add_log_record_processor(
        sdk_log_export.BatchLogRecordProcessor(exp)
    )
    handler = OtlpStdlibLogHandler(logger_provider=logger_prov)
    with _LOGGING_LOCK:
        for name in ("agentops_sdk", "agentops"):
            pkg_logger = logging.getLogger(name)
            if pkg_logger.level == logging.NOTSET:
                pkg_logger.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
    return logger_prov, handler
