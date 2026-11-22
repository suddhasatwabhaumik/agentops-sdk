# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for telemetry exporters, decorators, and CaseTracker."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from google.cloud import bigquery
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
import pytest

from agentops_sdk.domain import context as domain_context
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import models
from agentops_sdk.telemetry import BatchedGCSTelemetryExporter
from agentops_sdk.telemetry import BigQueryTelemetryExporter
from agentops_sdk.telemetry import CloudLoggingTelemetryExporter
from agentops_sdk.telemetry import InMemoryTelemetryExporter
from agentops_sdk.telemetry import LocalFileTelemetryExporter
from agentops_sdk.telemetry import UseCaseTracker
from agentops_sdk.telemetry import emit_finops_telemetry
from agentops_sdk.telemetry import gcp_exporters
from agentops_sdk.telemetry import otlp_exporters
from agentops_sdk.telemetry import trace_agent_flow
from agentops_sdk.telemetry import trace_agent_span
from agentops_sdk.telemetry import trace_llm_call
from agentops_sdk.telemetry import trace_use_case


def test_gcp_exporters_partitioning_and_otlp_json_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    assert gcp_exporters.sanitize_gcs_segment("../evil/seg") == "evil-seg"
    with pytest.raises(ValueError):
        BatchedGCSTelemetryExporter("valid-bucket", batch_size=0)
    otlp_exporters.set_active_mode(models.TelemetryMode.OTLP)
    assert CloudLoggingTelemetryExporter().export(
        models.UseCaseTelemetry(use_case_name="uc")
    )
    json_recs = [r for r in caplog.records if hasattr(r, "json_fields")]
    assert json_recs[-1].json_fields["event_type"] == "use_case_telemetry"

    created: list[Any] = []
    bq = SimpleNamespace(
        create_table=lambda t, **_kw: created.append(t),
        insert_rows_json=lambda *_a, **_kw: [{"err": "bad"}],
    )
    with pytest.raises(exceptions.TelemetryExportError):
        BigQueryTelemetryExporter("test-proj", client=bq).export(
            models.UseCaseTelemetry(use_case_name="uc")
        )
    assert created[0].time_partitioning.type_ == (
        bigquery.TimePartitioningType.DAY
    )
    assert created[0].time_partitioning.field == "timestamp"


def test_local_file_telemetry_exporter_oserror_and_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with LocalFileTelemetryExporter(
        tmp_path / "tel.jsonl", base_dir=tmp_path
    ) as exp:
        orig_stream = exp._handler.stream
        bad_stream = SimpleNamespace(
            tell=lambda: 0,
            seek=lambda *_a: 0,
            write=lambda *_a: (_ for _ in ()).throw(OSError("io fail")),
            flush=lambda: None,
            close=orig_stream.close,
        )
        monkeypatch.setattr(exp._handler, "stream", bad_stream)
        with pytest.raises(exceptions.TelemetryExportError, match="io fail"):
            exp.export(models.UseCaseTelemetry(use_case_name="uc"))
    assert exp._handler not in exp._log.handlers


async def test_decorators_preserve_verified_identity(
    otel_span_exporter: InMemorySpanExporter,
) -> None:
    sink = InMemoryTelemetryExporter()

    @trace_agent_flow(flow_name="qa", agent_role="advisor", kpi_id="Sales KPI")
    @trace_agent_span(agent_name="worker")
    @trace_llm_call(model_name_override="gemini-2.5-flash")
    async def run_qa(prompt: str, *, session_id: str, user_id: str) -> Any:
        del prompt, session_id, user_id
        return {"usage_metadata": {"prompt_token_count": 7}}

    @trace_use_case("uc_flow", exporter=sink)
    def tracked_op() -> str:
        return "done"

    with domain_context.bind_execution_scope(
        user_id="verified-alice", claims={"sub": "verified-alice"}
    ):
        await run_qa("hello", session_id="s-9", user_id="spoofed-u")
    assert tracked_op() == "done" and len(sink.records) == 1
    spans = otel_span_exporter.get_finished_spans()
    flow_span = next(s for s in spans if s.name == "agent_flow:qa")
    assert flow_span.attributes["agentops.user.id"] == "verified-alice"
    assert {s.name for s in spans} >= {
        "agent_flow:qa",
        "agent_run:worker",
        "llm_call:run_qa",
        "use_case:uc_flow",
    }


def test_case_tracker_and_finops_redaction() -> None:
    sink = InMemoryTelemetryExporter()
    with UseCaseTracker("bad", exporter=sink) as tr:
        with pytest.raises(ValueError):
            tr.end_stage("missing")
        with pytest.raises(ValueError):
            tr.add_transactions(-1)
    scope_stub: Any = SimpleNamespace(session_id="s", user_id="u")
    fin_rec = emit_finops_telemetry(
        models.LLMUsageMetric(status="ERROR", error_message="Bearer tok"),
        telemetry_context=models.TelemetryContext(),
        scope=scope_stub,
    )
    assert fin_rec["error_message"].endswith("[REDACTED_TOKEN]")
