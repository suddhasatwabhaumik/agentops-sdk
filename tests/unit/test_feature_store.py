# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for agentops_sdk.feature_store client, admin, and protos."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from google.api_core import exceptions as gcp_exceptions
from google.cloud import aiplatform_v1
from google.protobuf import timestamp_pb2
from google.rpc import status_pb2
from google.type import interval_pb2
import pytest

from agentops_sdk.domain import exceptions
from agentops_sdk.feature_store import FeatureStoreAdminClient
from agentops_sdk.feature_store import FeatureStoreOnlineClient
from agentops_sdk.feature_store import InMemoryFeatureOnlineStore
from agentops_sdk.feature_store import _protos
from agentops_sdk.feature_store import extract_feature_value
from agentops_sdk.feature_store import normalize_entity_key


def test_normalize_entity_key_and_extract_feature_value() -> None:
    assert normalize_entity_key(" sku-1 ") == "sku-1"
    assert normalize_entity_key(["tenant-1", "sku-1"]) == "tenant-1:sku-1"
    assert extract_feature_value(None) is None
    assert extract_feature_value(object()) is None
    assert extract_feature_value(aiplatform_v1.FeatureValue()) is None
    fv = aiplatform_v1.FeatureValue(
        double_array_value=aiplatform_v1.DoubleArray(values=[1.5, 2.5])
    )
    assert extract_feature_value(fv) == [1.5, 2.5]
    mem = InMemoryFeatureOnlineStore()
    mem.seed_features("st", "vw", {"k1": {"a": 1}})
    mem.clear()
    assert mem.read_online_features("st", "vw", entity_ids=["k1"]) == {"k1": {}}


def test_online_client_endpoint_caching_and_errors() -> None:
    factory_calls: list[str] = []
    admin_stub = SimpleNamespace(
        get_feature_online_store=lambda **_kw: SimpleNamespace(
            dedicated_serving_endpoint=SimpleNamespace(
                public_endpoint_domain_name=""
            )
        )
    )
    data_stub = SimpleNamespace(
        fetch_feature_values=lambda **_kw: (_ for _ in ()).throw(
            RuntimeError("RPC timeout")
        )
    )

    def _factory(*, client_options: Any) -> Any:
        factory_calls.append(client_options.api_endpoint)
        return data_stub

    client = FeatureStoreOnlineClient(
        project_id="test-proj",
        location="europe-west4",
        admin_client=admin_stub,
        data_client_factory=_factory,
    )
    with pytest.raises(exceptions.FeatureStoreError, match="Failed reading"):
        client.read_online_features("store-1", "view-1", entity_ids=["e1"])
    assert factory_calls == ["europe-west4-aiplatform.googleapis.com"]
    # Second call reuses cached domain and data client
    with pytest.raises(exceptions.FeatureStoreError):
        client.read_online_features("store-1", "view-1", entity_ids=["e1"])
    assert len(factory_calls) == 1


def test_protos_sync_and_store_builders() -> None:
    s_run = aiplatform_v1.FeatureViewSync(
        run_time=interval_pb2.Interval(
            start_time=timestamp_pb2.Timestamp(seconds=100)
        )
    )
    s_ok = aiplatform_v1.FeatureViewSync(
        run_time=interval_pb2.Interval(
            start_time=timestamp_pb2.Timestamp(seconds=100),
            end_time=timestamp_pb2.Timestamp(seconds=200),
        ),
        final_status=status_pb2.Status(code=0),
    )
    s_fail = aiplatform_v1.FeatureViewSync(
        run_time=interval_pb2.Interval(
            start_time=timestamp_pb2.Timestamp(seconds=100),
            end_time=timestamp_pb2.Timestamp(seconds=200),
        ),
        final_status=status_pb2.Status(code=13),
    )
    assert _protos.resolve_sync_state(SimpleNamespace(run_time=None)) == (
        "RUNNING"
    )
    assert _protos.resolve_sync_state(s_run) == "RUNNING"
    assert _protos.resolve_sync_state(s_ok) == "SUCCEEDED"
    assert _protos.resolve_sync_state(s_fail) == "FAILED"
    opt = _protos.build_store_proto("optimized", 1, 1)
    assert "optimized" in opt
    bt = _protos.build_store_proto("bigtable", 1, 3)
    assert bt.bigtable.auto_scaling.min_node_count == 1
    assert bt.bigtable.auto_scaling.max_node_count == 3
    with pytest.raises(ValueError):
        _protos.build_store_proto("bigtable", 3, 1)
    with pytest.raises(ValueError):
        _protos.build_store_proto("bad", 1, 1)  # type: ignore[arg-type]


def test_protos_view_and_poll_sync() -> None:
    with pytest.raises(ValueError):
        _protos.build_view_proto("v", "", ("id",), None, None)
    view, paths = _protos.build_view_proto(
        "v",
        "proj.ds.tbl",
        ("id",),
        "0 * * * *",
        {"embedding_column": "vec", "embedding_dimension": 128},
    )
    assert view.big_query_source.uri == "bq://proj.ds.tbl"
    assert paths == ["big_query_source", "sync_config", "index_config"]
    with pytest.raises(exceptions.FeatureStoreError, match="Sync timeout"):
        _protos.poll_sync(
            SimpleNamespace(
                get_feature_view_sync=lambda **_kw: SimpleNamespace(
                    run_time=None
                )
            ),
            "sync-1",
            0.1,
            0.05,
        )


def test_admin_client_existing_store_and_view() -> None:
    fake_admin = SimpleNamespace(
        get_feature_online_store=lambda **_kw: SimpleNamespace(name="store-ok"),
        get_feature_view=lambda **_kw: (_ for _ in ()).throw(
            gcp_exceptions.NotFound("no view")
        ),
        create_feature_view=lambda **_kw: SimpleNamespace(
            result=lambda **_r: SimpleNamespace(name="view-created")
        ),
    )
    admin = FeatureStoreAdminClient(
        "test-proj", "europe-west4", admin_client=fake_admin
    )
    assert admin.ensure_online_store("store-1")["created"] is False
    assert (
        admin.ensure_feature_view(
            "store-1", "view-1", bigquery_uri="bq://p.d.t"
        )["created"]
        is True
    )


def test_admin_client_sync_schedule_ops() -> None:
    fake_admin = SimpleNamespace(
        update_feature_view=lambda **_kw: SimpleNamespace(
            result=lambda **_r: SimpleNamespace(name="view-sched")
        ),
        get_feature_view=lambda **_kw: SimpleNamespace(
            sync_config=SimpleNamespace(cron="0 * * * *")
        ),
        list_feature_view_syncs=lambda **_kw: [
            SimpleNamespace(
                name="sync-latest",
                run_time=SimpleNamespace(end_time=5),
                final_status=SimpleNamespace(code=0),
            )
        ],
    )
    admin = FeatureStoreAdminClient(
        "test-proj", "europe-west4", admin_client=fake_admin
    )
    sched = admin.set_sync_schedule(
        "store-1", "view-1", cron_schedule="0 * * * *"
    )
    assert sched["cron_schedule"] == "0 * * * *"
    info = admin.get_sync_schedule("store-1", "view-1")
    assert info["last_sync_state"] == "SUCCEEDED"
    assert info["cron_schedule"] == "0 * * * *"
