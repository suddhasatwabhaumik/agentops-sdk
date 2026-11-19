# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for tools.feature_store_migration steps and orchestrator."""

from __future__ import annotations

from typing import Any

from google.cloud import bigquery
from google.cloud import exceptions as cloud_exceptions
import pytest

from agentops_sdk.domain import exceptions
from tools.feature_store_migration import step1_seed_dummy_data
from tools.feature_store_migration import step2_setup_feature_store
from tools.feature_store_migration import step3_trigger_sync
from tools.feature_store_migration import step4_manage_sync_schedule
from tools.feature_store_migration import step5_verify_online_lookup
from tools.feature_store_migration import sync_orchestrator


class _FakeSeedBQClient:
    def __init__(self, *, insert_errors: list[Any] | None = None) -> None:
        self.created_tables: list[Any] = []
        self.inserted: list[tuple[str, list[dict[str, Any]]]] = []
        self._errors = insert_errors or []

    def get_dataset(self, _ref: Any) -> Any:
        raise cloud_exceptions.NotFound("ds")

    def create_dataset(self, _ds: Any, **_kw: Any) -> None:
        return None

    def get_table(self, _ref: Any) -> Any:
        raise cloud_exceptions.NotFound("tbl")

    def create_table(self, tbl: Any, **_kw: Any) -> None:
        self.created_tables.append(tbl)

    def insert_rows_json(
        self, ref: str, rows: list[dict[str, Any]]
    ) -> list[Any]:
        self.inserted.append((ref, rows))
        return list(self._errors)


class _RecordingAdminStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def ensure_online_store(self, store: str, **kw: Any) -> dict[str, Any]:
        self.calls.append(("ensure_online_store", {"store": store, **kw}))
        return {"name": store, "created": True}

    def ensure_feature_view(
        self, store: str, view: str, **kw: Any
    ) -> dict[str, Any]:
        self.calls.append(
            ("ensure_feature_view", {"store": store, "view": view, **kw})
        )
        return {"name": view, "created": True}

    def trigger_sync(self, store: str, view: str, **kw: Any) -> dict[str, str]:
        self.calls.append(
            ("trigger_sync", {"store": store, "view": view, **kw})
        )
        return {"sync_name": "s1", "sync_state": "SUCCEEDED"}

    def set_sync_schedule(
        self, store: str, view: str, **kw: Any
    ) -> dict[str, Any]:
        self.calls.append(
            ("set_sync_schedule", {"store": store, "view": view, **kw})
        )
        return {"cron_schedule": kw.get("cron_schedule")}

    def get_sync_schedule(self, store: str, view: str) -> dict[str, Any]:
        self.calls.append(("get_sync_schedule", {"store": store, "view": view}))
        return {
            "feature_view": view,
            "cron_schedule": "0 * * * *",
            "last_sync_name": "sync-99",
            "last_sync_state": "SUCCEEDED",
        }


def test_step1_schema_generation_and_seeding() -> None:
    fields = step1_seed_dummy_data.build_feature_schema(
        feature_columns={"qty": "INT", "ok": "BOOL"}
    )
    assert [f.field_type for f in fields] == [
        "STRING",
        "TIMESTAMP",
        "INTEGER",
        "BOOLEAN",
    ]
    rows = step1_seed_dummy_data.generate_dummy_features(num_records=3)
    assert len(rows) == 3 and rows[0]["entity_id"] == "ENTITY-1001"
    with pytest.raises(ValueError):
        step1_seed_dummy_data.generate_dummy_features(num_records=0)

    dry = step1_seed_dummy_data.seed_dummy_data(
        "ds", "tbl", num_records=2, dry_run=True
    )
    assert dry["status"] == "simulated" and dry["records_loaded"] == 2

    bq_cli = _FakeSeedBQClient()
    live = step1_seed_dummy_data.seed_dummy_data(
        "ds", "tbl", project_id="test-proj", num_records=2, bq_client=bq_cli
    )
    assert live["status"] == "ready" and live["records_loaded"] == 2
    tbl = bq_cli.created_tables[0]
    assert len(tbl.schema) == 9
    assert tbl.time_partitioning.type_ == bigquery.TimePartitioningType.DAY
    assert tbl.time_partitioning.field == "feature_timestamp"

    err_bq = _FakeSeedBQClient(insert_errors=[{"error": "bad"}])
    with pytest.raises(exceptions.FeatureStoreError):
        step1_seed_dummy_data.load_records_to_bigquery(
            err_bq, "p", "ds", "tbl", rows
        )


def test_steps2_and_3_setup_and_sync() -> None:
    admin_stub = _RecordingAdminStub()
    s2_dry = step2_setup_feature_store.setup_feature_store(
        "ds", "tbl", "st", "vw", dry_run=True
    )
    assert s2_dry["status"] == "simulated"
    s2_live = step2_setup_feature_store.setup_feature_store(
        "ds",
        "tbl",
        "st",
        "vw",
        project_id="test-proj",
        admin_client=admin_stub,  # type: ignore[arg-type]
    )
    assert s2_live["status"] == "ready"
    assert (
        step3_trigger_sync.trigger_feature_view_sync(
            "st", "vw", wait_for_completion=True, dry_run=True
        )["sync_state"]
        == "SUCCEEDED"
    )
    assert (
        step3_trigger_sync.trigger_feature_view_sync(
            "st",
            "vw",
            project_id="test-proj",
            admin_client=admin_stub,  # type: ignore[arg-type]
        )["sync_state"]
        == "SUCCEEDED"
    )


def test_steps4_and_5_schedule_get_and_lookup(
    capsys: pytest.CaptureFixture[str],
) -> None:
    admin_stub = _RecordingAdminStub()
    with pytest.raises(ValueError):
        step4_manage_sync_schedule.manage_sync_schedule(
            "st", "vw", get_schedule=True, set_cron="0 * * * *"
        )
    got = step4_manage_sync_schedule.manage_sync_schedule(
        "st",
        "vw",
        project_id="test-proj",
        get_schedule=True,
        admin_client=admin_stub,  # type: ignore[arg-type]
    )
    assert got["last_sync_name"] == "sync-99"
    assert [c[0] for c in admin_stub.calls] == ["get_sync_schedule"]
    step4_manage_sync_schedule.main(
        ["--online-store", "st", "--feature-view", "vw", "--get", "--dry-run"]
    )
    assert "UNKNOWN (dry-run)" in capsys.readouterr().out

    s5_dry = step5_verify_online_lookup.verify_online_lookup(
        "st", "vw", entity_ids=["CUST_0001"], dry_run=True
    )
    assert s5_dry["status"] == "simulated"
    assert s5_dry["records"]["CUST_0001"]["stock_level"] == 110


def test_sync_orchestrator_skips_duplicate_step4_and_cli_mains(
    capsys: pytest.CaptureFixture[str],
) -> None:
    admin_stub = _RecordingAdminStub()
    out = sync_orchestrator.orchestrate_sync(
        "ds",
        "tbl",
        "st",
        "vw",
        project_id="test-proj",
        region="europe-west4",
        load_dummy_data=True,
        num_records=3,
        sync_cron="0 * * * *",
        bq_client=_FakeSeedBQClient(),
        admin_client=admin_stub,  # type: ignore[arg-type]
    )
    assert out["step1_seed"]["records_loaded"] == 3
    assert out["step4_schedule"]["configured_in_step2"] is True
    view_calls = [
        kw for op, kw in admin_stub.calls if op == "ensure_feature_view"
    ]
    assert view_calls[0]["wait_for_completion"] is True
    assert not any(op == "set_sync_schedule" for op, _ in admin_stub.calls)

    base = ["--dataset", "ds", "--table", "tbl", "--online-store", "st"]
    step1_seed_dummy_data.main([*base[:4], "--dry-run"])
    step2_setup_feature_store.main([*base, "--feature-view", "vw", "--dry-run"])
    step3_trigger_sync.main(
        ["--online-store", "st", "--feature-view", "vw", "--dry-run"]
    )
    step5_verify_online_lookup.main(
        ["--online-store", "st", "--feature-view", "vw", "--dry-run"]
    )
    sync_orchestrator.main(
        [*base, "--feature-view", "vw", "--load-dummy-data", "--dry-run"]
    )
    captured = capsys.readouterr().out
    assert "Step 1 Complete" in captured and "simulated" in captured
