# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for tools.observability LogRouterManager and log_exporter."""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from typing import Any

from google.api_core import exceptions as gcp_exceptions
from google.cloud import bigquery
from google.cloud import exceptions as cloud_exceptions
from google.cloud.logging_v2.types import logging_config
import pytest

from tools.observability import _sink_ops
from tools.observability import log_exporter
from tools.observability import log_router_admin


class _FakeBQClient:
    def __init__(self, *, missing_ds: bool = False) -> None:
        self.missing_ds = missing_ds
        self.created_datasets: list[Any] = []
        self.updated_datasets: list[tuple[Any, list[str]]] = []
        self.created_tables: list[Any] = []
        self.inserted_rows: list[tuple[str, list[dict[str, Any]]]] = []
        self.dataset_obj = SimpleNamespace(access_entries=[])

    def get_dataset(self, _ref: str) -> Any:
        if self.missing_ds:
            raise cloud_exceptions.NotFound("missing")
        return self.dataset_obj

    def create_dataset(self, ds: Any, **_kw: Any) -> None:
        self.created_datasets.append(ds)
        self.missing_ds = False

    def update_dataset(self, ds: Any, fields: list[str]) -> None:
        self.updated_datasets.append((ds, fields))

    def create_table(self, table: Any, **_kw: Any) -> None:
        self.created_tables.append(table)

    def insert_rows_json(
        self, ref: str, rows: list[dict[str, Any]]
    ) -> list[Any]:
        self.inserted_rows.append((ref, rows))
        return []


class _FakeGCSClient:
    def __init__(self, *, missing_bucket: bool = False) -> None:
        self.missing_bucket = missing_bucket
        self.created_buckets: list[tuple[Any, str]] = []
        self.uploads: list[tuple[str, str, str | None]] = []
        self.policies: list[Any] = []
        self.bucket_obj = SimpleNamespace(
            storage_class=None,
            versioning_enabled=False,
            iam_configuration=SimpleNamespace(
                uniform_bucket_level_access_enabled=False,
                public_access_prevention="",
            ),
            get_iam_policy=lambda **_kw: {"roles/storage.objectCreator": set()},
            set_iam_policy=lambda p: self.policies.append(p),
            blob=lambda key: SimpleNamespace(
                upload_from_string=lambda data, content_type=None: (
                    self.uploads.append((key, data, content_type))
                )
            ),
        )

    def get_bucket(self, _name: str) -> Any:
        if self.missing_bucket:
            raise cloud_exceptions.NotFound("missing")
        return self.bucket_obj

    def bucket(self, _name: str) -> Any:
        return self.bucket_obj

    def create_bucket(self, bucket: Any, *, location: str) -> None:
        self.created_buckets.append((bucket, location))
        self.missing_bucket = False


def _fake_logging_client(entries: list[Any] | None = None) -> SimpleNamespace:
    sink_obj = SimpleNamespace(
        name="bq-sink",
        destination="",
        filter_="",
        writer_identity="serviceAccount:log@gcp-sa.iam",
        exists=lambda: False,
        create=lambda **_kw: None,
        reload=lambda: None,
        update=lambda **_kw: None,
    )
    return SimpleNamespace(
        sink=lambda **_kw: sink_obj,
        list_sinks=lambda: [sink_obj],
        list_entries=lambda **_kw: entries or [],
        logger=lambda _n: SimpleNamespace(log_struct=lambda *_a, **_kw: None),
    )


def test_log_router_dataset_bucket_and_sinks() -> None:
    bq_cli = _FakeBQClient(missing_ds=True)
    gcs_cli = _FakeGCSClient(missing_bucket=True)
    mgr = log_router_admin.LogRouterManager(
        project_id="test-proj",
        region="europe-west4",
        dataset_id="obs_ds",
        bucket_name="obs-bucket",
        logging_client=_fake_logging_client(),
        bq_client=bq_cli,
        storage_client=gcs_cli,
    )
    assert mgr.ensure_bigquery_dataset() == "obs_ds"
    assert bq_cli.created_datasets[0].location == "europe-west4"
    assert mgr.ensure_gcs_bucket() == "obs-bucket"
    bkt, loc = gcs_cli.created_buckets[0]
    assert loc == "europe-west4" and bkt.storage_class == "STANDARD"
    assert bkt.iam_configuration.uniform_bucket_level_access_enabled is True
    assert bkt.iam_configuration.public_access_prevention == "enforced"
    assert bkt.versioning_enabled is True

    assert mgr.setup_bigquery_sink("bq-sink")["sink_name"] == "bq-sink"
    assert bq_cli.updated_datasets[0][1] == ["access_entries"]
    assert mgr.setup_gcs_sink("gcs-sink")["sink_name"] == "gcs-sink"
    assert (
        "serviceAccount:log@gcp-sa.iam"
        in (gcs_cli.policies[0]["roles/storage.objectCreator"])
    )


def _make_gapic_fake(reqs: list[tuple[str, Any]]) -> SimpleNamespace:
    has_sink = {"exists": False}

    def _get_sink(*, request: Any) -> Any:
        reqs.append(("get", request))
        if not has_sink["exists"]:
            raise gcp_exceptions.NotFound("missing")
        return SimpleNamespace(writer_identity="serviceAccount:g@gcp-sa.iam")

    def _create_sink(*, request: Any) -> Any:
        reqs.append(("create", request))
        has_sink["exists"] = True
        return SimpleNamespace(writer_identity="serviceAccount:g@gcp-sa.iam")

    def _update_sink(*, request: Any) -> Any:
        reqs.append(("update", request))
        return SimpleNamespace(writer_identity="serviceAccount:g@gcp-sa.iam")

    return SimpleNamespace(
        get_sink=_get_sink, create_sink=_create_sink, update_sink=_update_sink
    )


def test_log_router_gapic_create_and_update() -> None:
    reqs: list[tuple[str, Any]] = []
    mgr = log_router_admin.LogRouterManager(
        project_id="test-proj",
        region="europe-west4",
        dataset_id="obs_ds",
        bucket_name="obs-bucket",
        logging_client=_fake_logging_client(),
        bq_client=_FakeBQClient(),
        storage_client=_FakeGCSClient(),
        config_client=_make_gapic_fake(reqs),
    )
    assert mgr.setup_bigquery_sink("gapic-sink")["writer_identity"] == (
        "serviceAccount:g@gcp-sa.iam"
    )
    assert isinstance(reqs[1][1], logging_config.CreateSinkRequest)
    assert reqs[1][1].sink.bigquery_options.use_partitioned_tables is True
    mgr.setup_bigquery_sink("gapic-sink")
    assert isinstance(reqs[3][1], logging_config.UpdateSinkRequest)
    assert "bigquery_options" in reqs[3][1].update_mask.paths


def test_log_router_export_and_cli_list_sinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    entry = SimpleNamespace(
        insert_id="id-1",
        timestamp=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        severity="INFO",
        log_name="projects/p/logs/agentops-finops-telemetry",
        trace="t-1",
        span_id="s-1",
        payload={"event_type": "finops_llm_telemetry"},
    )
    bq_cli, gcs_cli = _FakeBQClient(), _FakeGCSClient()
    mgr = log_router_admin.LogRouterManager(
        project_id="test-proj",
        region="europe-west4",
        dataset_id="obs_ds",
        bucket_name="obs-bucket",
        logging_client=_fake_logging_client([entry]),
        bq_client=bq_cli,
        storage_client=gcs_cli,
    )
    out_file = tmp_path / "logs.jsonl"
    stats = mgr.export_logs(hours=1, limit=5, local_output_file=str(out_file))
    assert stats["retrieved_count"] == 1
    assert stat.S_IMODE(os.stat(out_file).st_mode) == 0o600
    tp = bq_cli.created_tables[0].time_partitioning
    assert tp.type_ == bigquery.TimePartitioningType.DAY
    assert tp.field == "timestamp"
    assert gcs_cli.uploads[0][2] == "application/x-ndjson"
    assert json.loads(gcs_cli.uploads[0][1].strip())["log_id"] == "id-1"
    assert mgr.emit_synthetic_test_log()["event_type"] == "finops_llm_telemetry"
    monkeypatch.setattr(log_router_admin, "LogRouterManager", lambda **_kw: mgr)
    log_router_admin.main(["--project", "test-proj", "list-sinks"])
    assert json.loads(capsys.readouterr().out.strip())["name"] == "bq-sink"


def test_sink_ops_and_log_exporter_guards(tmp_path: Path) -> None:
    assert _sink_ops.require_writer_identity(" sa ") == "sa"
    with pytest.raises(ValueError):
        _sink_ops.require_writer_identity("")
    outside = tmp_path / "outside.jsonl"
    outside.write_text("", encoding="utf-8")
    os.symlink(outside, tmp_path / "link.jsonl")
    with pytest.raises(ValueError, match="Symlink"):
        log_exporter.write_jsonl_file([], str(tmp_path / "link.jsonl"))
    bq_err = SimpleNamespace(
        create_table=lambda *_a, **_kw: None,
        insert_rows_json=lambda *_a, **_kw: [{"err": 1}],
    )
    rec = {
        "log_id": "1",
        "timestamp": "2026-01-01T00:00:00Z",
        "severity": "INFO",
        "log_name": "l",
        "trace_id": "t",
        "span_id": "s",
        "payload": {},
    }
    assert log_exporter.export_to_bigquery(bq_err, "p", "d", [rec]) == 0
