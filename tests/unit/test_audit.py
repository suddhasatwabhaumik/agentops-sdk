# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for agentops_sdk.audit ledger and sink adapters."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from google.cloud import bigquery
import pytest

from agentops_sdk.audit import AuditLedger
from agentops_sdk.audit import BigQueryAuditAdapter
from agentops_sdk.audit import CloudLoggingAuditAdapter
from agentops_sdk.audit import InMemoryAuditAdapter
from agentops_sdk.audit import LocalFileAuditAdapter
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import models
from agentops_sdk.telemetry import otlp_exporters


def _make_entry(error_message: str | None = None) -> models.AuditLogEntry:
    return models.AuditLogEntry(
        trace_id="trace-1",
        session_id="sess-1",
        user_id="user-1",
        agent_name="parts-agent",
        input_query="SELECT id FROM parts",
        generated_sql="SELECT id FROM parts WHERE tenant_id = 't1'",
        execution_status="ERROR" if error_message else "SUCCESS",
        error_message=error_message,
        latency_ms=4.5,
    )


async def test_audit_ledger_scrubs_error_sync_and_async() -> None:
    sink = InMemoryAuditAdapter()
    ledger = AuditLedger(sink=sink)
    assert ledger.sink is sink
    raw = "Failed for alice@example.com with Bearer secret-tok-123"
    assert ledger.log_entry_sync(_make_entry(raw)) is True
    assert await ledger.log_entry(_make_entry(raw)) is True
    assert len(sink.entries) == 2
    for item in sink.entries:
        assert item.error_message is not None
        assert "alice@example.com" not in item.error_message
        assert "secret-tok-123" not in item.error_message
    sink.clear()
    assert sink.entries == []


def test_cloud_logging_audit_modes_severity_and_json_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    calls: list[tuple[dict[str, Any], str]] = []
    client = SimpleNamespace(
        logger=lambda _n: SimpleNamespace(
            log_struct=lambda p, severity="INFO": calls.append((p, severity))
        )
    )
    adapter = CloudLoggingAuditAdapter(project_id="test-proj", client=client)
    assert adapter.write_entry(_make_entry()) is True
    assert calls[-1][1] == "INFO"
    assert adapter.write_entry(_make_entry("failed")) is True
    assert calls[-1][1] == "WARNING"

    otlp_exporters.set_active_mode(models.TelemetryMode.OTLP)
    otlp_only = CloudLoggingAuditAdapter(project_id="test-proj", client=client)
    before = len(calls)
    assert otlp_only.write_entry(_make_entry()) is True
    assert len(calls) == before
    audit_recs = [r for r in caplog.records if hasattr(r, "json_fields")]
    assert audit_recs[-1].json_fields["event_type"] == "audit_log_entry"
    assert audit_recs[-1].json_fields["agent_name"] == "parts-agent"


def test_bigquery_audit_adapter_creates_partitioned_table_once() -> None:
    created: list[Any] = []
    rows_written: list[dict[str, Any]] = []
    bq = SimpleNamespace(
        create_table=lambda tbl, **_kw: created.append(tbl),
        insert_rows_json=lambda _ref, rows, **_kw: (
            rows_written.extend(rows) or []
        ),
    )
    adapter = BigQueryAuditAdapter(
        project_id="test-proj",
        dataset_id="gov_ds",
        table_id="audit_tbl",
        client=bq,
    )
    assert adapter.write_entry(_make_entry()) is True
    assert adapter.write_entry(_make_entry()) is True
    assert len(created) == 1
    assert created[0].time_partitioning.type_ == (
        bigquery.TimePartitioningType.DAY
    )
    assert created[0].time_partitioning.field == "timestamp"
    assert len(rows_written) == 2

    with pytest.raises(ValueError):
        BigQueryAuditAdapter(project_id="p", dataset_id="../bad")


def test_local_file_audit_rotation_close_and_handle_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError):
        LocalFileAuditAdapter("../escape.jsonl", base_dir=tmp_path)
    target = tmp_path / "audit.jsonl"
    with LocalFileAuditAdapter(
        target, base_dir=tmp_path, max_bytes=120, backup_count=2
    ) as adapter:
        for _ in range(3):
            adapter.write_entry(_make_entry())
        assert target.exists()
        assert (tmp_path / "audit.jsonl.1").exists()
        orig_stream = adapter._handler.stream
        bad_stream = SimpleNamespace(
            tell=lambda: 0,
            seek=lambda *_a: 0,
            write=lambda *_a: (_ for _ in ()).throw(OSError("disk full")),
            flush=lambda: None,
            close=orig_stream.close,
        )
        monkeypatch.setattr(adapter._handler, "maxBytes", 10_000)
        monkeypatch.setattr(adapter._handler, "stream", bad_stream)
        with pytest.raises(exceptions.AuditLedgerError, match="disk full"):
            adapter.write_entry(_make_entry())
    assert adapter._handler not in adapter._logger.handlers


def test_audit_ledger_default_sink_and_error_wrapping() -> None:
    default_ledger = AuditLedger()
    assert isinstance(default_ledger.sink, CloudLoggingAuditAdapter)
    bad_bq = SimpleNamespace(
        create_table=lambda *_a, **_kw: (_ for _ in ()).throw(
            RuntimeError("BQ down")
        )
    )
    with pytest.raises(exceptions.AuditLedgerError, match="BQ audit write"):
        BigQueryAuditAdapter(project_id="p", client=bad_bq).write_entry(
            _make_entry()
        )
