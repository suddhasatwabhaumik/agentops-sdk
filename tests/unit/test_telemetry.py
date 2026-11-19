# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for agentops_sdk.telemetry providers, OTLP, and meters."""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry import metrics
from opentelemetry.metrics import _internal as metrics_internal
from opentelemetry.sdk import _logs as sdk_logs
from opentelemetry.sdk._logs import export as sdk_log_export
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
import pytest

from agentops_sdk.domain import exceptions
from agentops_sdk.domain import models
from agentops_sdk.telemetry import CachedMeterTracker
from agentops_sdk.telemetry import OtlpStdlibLogHandler
from agentops_sdk.telemetry import configure_telemetry
from agentops_sdk.telemetry import extract_llm_usage
from agentops_sdk.telemetry import gcp_exporters
from agentops_sdk.telemetry import get_active_telemetry_mode
from agentops_sdk.telemetry import log_llm_metrics
from agentops_sdk.telemetry import otlp_exporters
from agentops_sdk.telemetry import provider
from agentops_sdk.telemetry import record_fallback_metric
from agentops_sdk.telemetry import resolve_telemetry_mode
from agentops_sdk.telemetry import scrub_sensitive_text


def _stub_fast_otlp(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(
        otlp_exporters,
        "create_otlp_span_processor",
        lambda _u=None: SimpleSpanProcessor(InMemorySpanExporter()),
    )
    monkeypatch.setattr(
        otlp_exporters,
        "create_otlp_metric_reader",
        lambda _u=None: InMemoryMetricReader(),
    )

    def _cfg_logs(res: Any, _u: Any = None) -> tuple[Any, logging.Handler]:
        lp = sdk_logs.LoggerProvider(resource=res, shutdown_on_exit=False)
        lp.add_log_record_processor(
            sdk_log_export.SimpleLogRecordProcessor(
                sdk_log_export.InMemoryLogRecordExporter()
            )
        )
        h = OtlpStdlibLogHandler(lp)
        logging.getLogger().addHandler(h)
        orig_shutdown = lp.shutdown
        lp.shutdown = lambda: (events.append("lp_shutdown"), orig_shutdown())[1]
        return lp, h

    monkeypatch.setattr(otlp_exporters, "configure_otlp_logging", _cfg_logs)
    return events


def test_resolve_and_configure_telemetry_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("GOOGLE_CLOUD_PROJECT", "GCP_PROJECT", "OTEL_COLLECTOR_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        "agentops_sdk.config.settings.google.auth.default",
        lambda: (object(), None),
    )
    assert resolve_telemetry_mode("auto") == models.TelemetryMode.OFF
    assert (
        resolve_telemetry_mode("auto", collector_url="http://127.0.0.1:4317")
        == models.TelemetryMode.OTLP
    )
    assert (
        resolve_telemetry_mode("auto", project_id="test-proj")
        == models.TelemetryMode.GCP
    )
    with pytest.raises(exceptions.ConfigurationError):
        resolve_telemetry_mode("invalid-mode")

    _stub_fast_otlp(monkeypatch)
    off_h = configure_telemetry(mode="off")
    assert off_h.mode == models.TelemetryMode.OFF
    assert get_active_telemetry_mode() == models.TelemetryMode.OFF

    otlp_h = configure_telemetry(
        mode="otlp",
        service_name="unit-svc",
        collector_url="http://127.0.0.1:4318",
    )
    assert otlp_h.mode == models.TelemetryMode.OTLP
    assert get_active_telemetry_mode() == models.TelemetryMode.OTLP
    otlp_h.flush()
    otlp_h.shutdown()


def test_reset_telemetry_proxy_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fast_otlp(monkeypatch)
    monkeypatch.setattr(
        gcp_exporters,
        "create_gcp_span_processor",
        lambda _p: SimpleSpanProcessor(InMemorySpanExporter()),
    )
    monkeypatch.setattr(
        gcp_exporters,
        "create_gcp_metric_reader",
        lambda _p: InMemoryMetricReader(),
    )
    handles = configure_telemetry(
        mode="hybrid",
        project_id="test-proj",
        collector_url="http://127.0.0.1:4318",
    )
    assert handles.mode == models.TelemetryMode.HYBRID
    provider.reset_telemetry()
    assert isinstance(
        metrics.get_meter_provider(), metrics_internal._ProxyMeterProvider
    )
    assert not any(
        isinstance(h, OtlpStdlibLogHandler)
        for h in logging.getLogger().handlers
    )


def test_hybrid_partial_rollback_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _stub_fast_otlp(monkeypatch)
    monkeypatch.setattr(
        gcp_exporters,
        "create_gcp_span_processor",
        lambda _p: SimpleSpanProcessor(InMemorySpanExporter()),
    )
    monkeypatch.setattr(
        gcp_exporters,
        "create_gcp_metric_reader",
        lambda _p: (_ for _ in ()).throw(RuntimeError("gcp reader failed")),
    )
    with pytest.raises(RuntimeError, match="gcp reader failed"):
        configure_telemetry(
            mode="hybrid",
            project_id="test-proj",
            collector_url="http://127.0.0.1:4318",
        )
    assert "lp_shutdown" in events
    assert not any(
        isinstance(h, OtlpStdlibLogHandler)
        for h in logging.getLogger().handlers
    )


def test_otlp_helpers_and_stdlib_log_handler() -> None:
    scrubbed = scrub_sensitive_text(
        "Bearer abc.def.ghi email alice@example.com phone 555-123-4567"
    )
    assert "abc.def.ghi" not in scrubbed
    assert "alice@example.com" not in scrubbed
    assert "555-123-4567" not in scrubbed
    assert len(scrub_sensitive_text("x" * 100, max_length=20)) <= 35

    url, insecure, http = otlp_exporters.parse_otlp_endpoint(
        "http://127.0.0.1:4318/v1/traces"
    )
    assert (insecure, http) == (True, True)
    assert (
        otlp_exporters.resolve_http_signal_endpoint(url, "metrics")
        == "http://127.0.0.1:4318/v1/metrics"
    )

    exp = sdk_log_export.InMemoryLogRecordExporter()
    lp = sdk_logs.LoggerProvider(shutdown_on_exit=False)
    lp.add_log_record_processor(sdk_log_export.SimpleLogRecordProcessor(exp))
    handler = OtlpStdlibLogHandler(lp)
    rec = logging.LogRecord(
        "agentops_sdk.test", logging.WARNING, __file__, 1, "hi", (), None
    )
    rec.json_fields = {"nested": {"k": "v"}, "count": 2}
    handler.emit(rec)
    handler.emit(
        logging.LogRecord(
            "urllib3.pool", logging.INFO, __file__, 1, "x", (), None
        )
    )
    finished = exp.get_finished_logs()
    assert len(finished) == 1
    assert finished[0].log_record.severity_text == "WARN"
    assert finished[0].log_record.attributes["nested.k"] == "v"


def test_meter_tracker_and_finops_usage() -> None:
    reader = InMemoryMetricReader()
    mp = MeterProvider(metric_readers=[reader])
    tracker = CachedMeterTracker(meter=mp.get_meter("test"))
    tracker.add_counter("c.test", 2, {"k": "v"})
    tracker.record_histogram("h.test", 1.5)
    tracker.add_up_down_counter("u.test", -1)
    tracker.clear()
    record_fallback_metric("comp", "reason")
    assert reader.get_metrics_data() is not None

    usage = extract_llm_usage(
        {
            "model_version": "gemini-2.5-flash",
            "usage_metadata": {
                "prompt_token_count": 10,
                "candidates_token_count": 20,
                "cached_content_token_count": 5,
            },
        },
        latency_ms=12.3456,
    )
    assert (usage.input_tokens, usage.output_tokens, usage.cached_tokens) == (
        10,
        20,
        5,
    )
    log_llm_metrics(
        model_name="gemini-2.5-flash", input_tokens=3, output_tokens=4
    )
