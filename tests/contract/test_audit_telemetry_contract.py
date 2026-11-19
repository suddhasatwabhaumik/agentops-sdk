# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""LSP contract tests for AuditSinkPort and CaseTelemetryExporterPort."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from typing import Any

import pytest

from agentops_sdk.audit import AuditLedger
from agentops_sdk.audit import BigQueryAuditAdapter
from agentops_sdk.audit import CloudLoggingAuditAdapter
from agentops_sdk.audit import InMemoryAuditAdapter
from agentops_sdk.audit import LocalFileAuditAdapter
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import models
from agentops_sdk.ports import audit as audit_ports
from agentops_sdk.ports import telemetry as telemetry_ports
from agentops_sdk.telemetry import BatchedGCSTelemetryExporter
from agentops_sdk.telemetry import BigQueryTelemetryExporter
from agentops_sdk.telemetry import CloudLoggingTelemetryExporter
from agentops_sdk.telemetry import CompositeTelemetryExporter
from agentops_sdk.telemetry import InMemoryTelemetryExporter
from agentops_sdk.telemetry import LocalFileTelemetryExporter
from agentops_sdk.telemetry import UseCaseTracker
from agentops_sdk.telemetry import _sink_helpers
from agentops_sdk.telemetry import otlp_exporters


def _fake_cl_client() -> SimpleNamespace:
    logged: list[tuple[str, dict[str, Any], str]] = []
    return SimpleNamespace(
        logged=logged,
        logger=lambda name: SimpleNamespace(
            log_struct=lambda p, severity="INFO": logged.append(
                (name, p, severity)
            )
        ),
    )


def _fake_bq_client(errors: list[Any] | None = None) -> SimpleNamespace:
    created: list[Any] = []
    rows: list[dict[str, Any]] = []
    errs = errors or []
    return SimpleNamespace(
        created_tables=created,
        rows=rows,
        create_table=lambda t, **_kw: created.append(t),
        insert_rows_json=lambda _r, rs, **_kw: rows.extend(rs) or list(errs),
    )


def _fake_gcs_client() -> SimpleNamespace:
    uploads: list[tuple[str, str]] = []
    return SimpleNamespace(
        uploads=uploads,
        bucket=lambda _b: SimpleNamespace(
            blob=lambda k: SimpleNamespace(
                upload_from_string=lambda d, **_kw: uploads.append((k, d))
            )
        ),
    )


def _sample_audit() -> models.AuditLogEntry:
    return models.AuditLogEntry(
        trace_id="t-1",
        session_id="s-1",
        user_id="u-1",
        agent_name="sql-agent",
        input_query="Show revenue",
        execution_status="SUCCESS",
    )


def _sample_telemetry(case_id: str = "case-1") -> models.UseCaseTelemetry:
    return models.UseCaseTelemetry(
        case_id=case_id, use_case_name="parts_lookup", success=True
    )


@pytest.fixture
def fake_sinks() -> SimpleNamespace:
    """Provides shared fake GCP clients for persistence assertions."""
    return SimpleNamespace(
        cl=_fake_cl_client(), bq=_fake_bq_client(), gcs=_fake_gcs_client()
    )


@pytest.fixture(params=["in_memory", "local_file", "cloud_logging", "bigquery"])
def audit_sink(
    request: pytest.FixtureRequest, tmp_path: Path, fake_sinks: SimpleNamespace
) -> audit_ports.AuditSinkPort:
    kind = request.param
    if kind == "in_memory":
        return InMemoryAuditAdapter()
    if kind == "local_file":
        return LocalFileAuditAdapter(
            tmp_path / "audit.jsonl", base_dir=tmp_path
        )
    if kind == "cloud_logging":
        return CloudLoggingAuditAdapter("test-proj", client=fake_sinks.cl)
    return BigQueryAuditAdapter("test-proj", client=fake_sinks.bq)


@pytest.fixture(
    params=[
        "in_memory",
        "local_file",
        "cloud_logging",
        "bigquery",
        "batched_gcs",
        "composite",
    ]
)
def telemetry_exporter(
    request: pytest.FixtureRequest, tmp_path: Path, fake_sinks: SimpleNamespace
) -> telemetry_ports.CaseTelemetryExporterPort:
    kind = request.param
    if kind == "in_memory":
        return InMemoryTelemetryExporter()
    if kind == "local_file":
        return LocalFileTelemetryExporter(
            tmp_path / "telemetry.jsonl", base_dir=tmp_path
        )
    if kind == "cloud_logging":
        return CloudLoggingTelemetryExporter("test-proj", client=fake_sinks.cl)
    if kind == "bigquery":
        return BigQueryTelemetryExporter("test-proj", client=fake_sinks.bq)
    if kind == "batched_gcs":
        return BatchedGCSTelemetryExporter(
            "unit-telemetry-bucket",
            batch_size=1,
            project_id="test-proj",
            client=fake_sinks.gcs,
        )
    return CompositeTelemetryExporter(
        [
            InMemoryTelemetryExporter(),
            LocalFileTelemetryExporter(
                tmp_path / "composite.jsonl", base_dir=tmp_path
            ),
        ]
    )


async def test_audit_sink_contract_sync_and_async(
    audit_sink: audit_ports.AuditSinkPort,
    tmp_path: Path,
    fake_sinks: SimpleNamespace,
) -> None:
    assert isinstance(audit_sink, audit_ports.AuditSinkPort)
    entry = _sample_audit()
    assert audit_sink.write_entry(entry) is True
    ledger = AuditLedger(sink=audit_sink)
    assert ledger.log_entry_sync(entry) is True
    assert await ledger.log_entry(entry) is True
    if isinstance(audit_sink, InMemoryAuditAdapter):
        assert len(audit_sink.entries) == 3
    elif isinstance(audit_sink, LocalFileAuditAdapter):
        assert len((tmp_path / "audit.jsonl").read_text().splitlines()) == 3
    elif isinstance(audit_sink, CloudLoggingAuditAdapter):
        assert len(fake_sinks.cl.logged) == 3
    else:
        assert len(fake_sinks.bq.created_tables) == 1
        assert len(fake_sinks.bq.rows) == 3


def test_local_file_audit_symlink_permissions_and_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside.jsonl"
    outside.write_text("", encoding="utf-8")
    os.symlink(outside, tmp_path / "link.jsonl")
    with pytest.raises(ValueError, match="Symlink"):
        LocalFileAuditAdapter(tmp_path / "link.jsonl", base_dir=tmp_path)

    log_path = tmp_path / "audit_secure.jsonl"
    adapter = LocalFileAuditAdapter(log_path, base_dir=tmp_path)
    assert adapter.write_entry(_sample_audit()) is True
    assert adapter.write_entry(_sample_audit()) is True
    assert stat.S_IMODE(os.stat(log_path).st_mode) == 0o600
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["agent_name"] == "sql-agent"

    monkeypatch.setattr(
        _sink_helpers,
        "write_local_jsonl_record",
        lambda *_a: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(exceptions.AuditLedgerError, match="disk full"):
        adapter.write_entry(_sample_audit())


def test_gcp_audit_adapters_propagate_errors() -> None:
    bad_cl = SimpleNamespace(
        logger=lambda _n: SimpleNamespace(
            log_struct=lambda *_a, **_kw: (_ for _ in ()).throw(
                RuntimeError("logging down")
            )
        )
    )
    with pytest.raises(exceptions.AuditLedgerError):
        CloudLoggingAuditAdapter(
            project_id="test-proj", client=bad_cl
        ).write_entry(_sample_audit())

    bad_bq = _fake_bq_client(errors=[{"errors": [{"reason": "invalid"}]}])
    with pytest.raises(exceptions.AuditLedgerError):
        BigQueryAuditAdapter(project_id="test-proj", client=bad_bq).write_entry(
            _sample_audit()
        )


def test_telemetry_mode_audit_vs_telemetry_routing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    cl = _fake_cl_client()
    monkeypatch.setattr(_sink_helpers.cloud_logging, "Client", lambda **_kw: cl)
    audit = CloudLoggingAuditAdapter(project_id="test-proj")
    telem = CloudLoggingTelemetryExporter(project_id="test-proj")

    otlp_exporters.set_active_mode(models.TelemetryMode.OFF)
    assert audit.write_entry(_sample_audit()) is True
    assert len(cl.logged) == 1
    assert telem.export(_sample_telemetry()) is True
    assert len(cl.logged) == 1

    otlp_exporters.set_active_mode(models.TelemetryMode.GCP)
    assert telem.export(_sample_telemetry()) is True
    assert len(cl.logged) == 2

    otlp_exporters.set_active_mode(models.TelemetryMode.OTLP)
    assert audit.write_entry(_sample_audit()) is True
    assert telem.export(_sample_telemetry()) is True
    assert len(cl.logged) == 2
    assert any("AuditLogEntry" in rec.message for rec in caplog.records)


async def test_telemetry_exporter_contract(
    telemetry_exporter: telemetry_ports.CaseTelemetryExporterPort,
    tmp_path: Path,
    fake_sinks: SimpleNamespace,
) -> None:
    assert isinstance(
        telemetry_exporter, telemetry_ports.CaseTelemetryExporterPort
    )
    assert telemetry_exporter.export(_sample_telemetry()) is True
    with UseCaseTracker("lookup", exporter=telemetry_exporter) as tracker:
        tracker.start_stage("parse")
        tracker.end_stage("parse")
        tracker.add_transactions(1)
    assert tracker.record is not None and tracker.record.success is True
    async with UseCaseTracker("alookup", exporter=telemetry_exporter) as atr:
        atr.set_metadata("k", "v")
    assert atr.record is not None and atr.record.metadata == {"k": "v"}
    if isinstance(telemetry_exporter, InMemoryTelemetryExporter):
        assert len(telemetry_exporter.records) == 3
    elif isinstance(telemetry_exporter, LocalFileTelemetryExporter):
        assert len((tmp_path / "telemetry.jsonl").read_text().splitlines()) == 3
    elif isinstance(telemetry_exporter, CloudLoggingTelemetryExporter):
        assert len(fake_sinks.cl.logged) == 3
    elif isinstance(telemetry_exporter, BigQueryTelemetryExporter):
        assert len(fake_sinks.bq.created_tables) == 1
        assert len(fake_sinks.bq.rows) == 3
    elif isinstance(telemetry_exporter, BatchedGCSTelemetryExporter):
        assert len(fake_sinks.gcs.uploads) == 3
    else:
        assert len((tmp_path / "composite.jsonl").read_text().splitlines()) == 3


def test_batched_gcs_telemetry_batching_and_flush() -> None:
    fake_gcs = _fake_gcs_client()
    exp = BatchedGCSTelemetryExporter(
        "unit-telemetry-bucket",
        batch_size=2,
        project_id="test-proj",
        client=fake_gcs,
    )
    assert exp.export(_sample_telemetry("c1")) is True
    assert fake_gcs.uploads == []
    assert exp.export(_sample_telemetry("c2")) is True
    assert len(fake_gcs.uploads) == 1
    assert len(fake_gcs.uploads[0][1].strip().splitlines()) == 2
    assert exp.export(_sample_telemetry("c3")) is True
    assert len(fake_gcs.uploads) == 1
    assert exp.flush() is True
    assert len(fake_gcs.uploads) == 2
    assert len(fake_gcs.uploads[1][1].strip().splitlines()) == 1
