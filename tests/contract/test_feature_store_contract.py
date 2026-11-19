# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""LSP contract tests for FeatureOnlineStorePort and FeatureStoreAdmin."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from google.api_core import exceptions as gcp_exceptions
from google.cloud import aiplatform_v1
import pytest

from agentops_sdk.domain import exceptions
from agentops_sdk.feature_store import FeatureStoreAdminClient
from agentops_sdk.feature_store import FeatureStoreOnlineClient
from agentops_sdk.feature_store import InMemoryFeatureOnlineStore
from agentops_sdk.ports import feature_store as fs_ports

_FV_DATA: dict[str, Any] = {
    "flag": True,
    "count": 42,
    "score": 0.95,
    "tier": "enterprise",
    "blob": b"raw-bytes",
    "flags": [True, False],
    "counts": [1, 2, 3],
    "scores": [0.1, 0.2],
    "tags": ["a", "b"],
    "meta": {"region": "eu", "active": True},
}


def _to_proto_value(val: Any) -> aiplatform_v1.FeatureValue:
    if isinstance(val, bool):
        return aiplatform_v1.FeatureValue(bool_value=val)
    if isinstance(val, int):
        return aiplatform_v1.FeatureValue(int64_value=val)
    if isinstance(val, float):
        return aiplatform_v1.FeatureValue(double_value=val)
    if isinstance(val, str):
        return aiplatform_v1.FeatureValue(string_value=val)
    if isinstance(val, bytes):
        return aiplatform_v1.FeatureValue(bytes_value=val)
    if isinstance(val, dict):
        fields = [
            aiplatform_v1.StructFieldValue(name=k, value=_to_proto_value(v))
            for k, v in val.items()
        ]
        return aiplatform_v1.FeatureValue(
            struct_value=aiplatform_v1.StructValue(values=fields)
        )
    if isinstance(val, list):
        return _to_proto_array(val)
    raise TypeError(f"Unsupported test value: {val!r}")


def _to_proto_array(vals: list[Any]) -> aiplatform_v1.FeatureValue:
    if isinstance(vals[0], bool):
        return aiplatform_v1.FeatureValue(
            bool_array_value=aiplatform_v1.BoolArray(values=vals)
        )
    if isinstance(vals[0], int):
        return aiplatform_v1.FeatureValue(
            int64_array_value=aiplatform_v1.Int64Array(values=vals)
        )
    if isinstance(vals[0], float):
        return aiplatform_v1.FeatureValue(
            double_array_value=aiplatform_v1.DoubleArray(values=vals)
        )
    return aiplatform_v1.FeatureValue(
        string_array_value=aiplatform_v1.StringArray(values=vals)
    )


class _FakeDataClient:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self._rows = rows

    def fetch_feature_values(
        self, *, request: aiplatform_v1.FetchFeatureValuesRequest, **_kw: Any
    ) -> aiplatform_v1.FetchFeatureValuesResponse:
        dk = request.data_key
        key = dk.key or ":".join(dk.composite_key.parts)
        if key not in self._rows:
            raise gcp_exceptions.NotFound(key)
        pair_cls = (
            aiplatform_v1.FetchFeatureValuesResponse.FeatureNameValuePairList
        )
        pairs = [
            pair_cls.FeatureNameValuePair(
                name=fname, value=_to_proto_value(fval)
            )
            for fname, fval in self._rows[key].items()
        ]
        return aiplatform_v1.FetchFeatureValuesResponse(
            key_values=pair_cls(features=pairs)
        )


@pytest.fixture(params=["in_memory", "online_client"])
def feature_store(
    request: pytest.FixtureRequest,
) -> fs_ports.FeatureOnlineStorePort:
    seeded: dict[fs_ports.EntityKey, dict[str, Any]] = {
        "tenant-a:sku-1": dict(_FV_DATA),
        "tenant-a:sku-2": {"tier": "standard", "count": 7},
    }
    if request.param == "in_memory":
        store = InMemoryFeatureOnlineStore()
        store.seed_features("store-1", "view-1", seeded)
        return store
    admin_stub = SimpleNamespace(
        get_feature_online_store=lambda **_kw: SimpleNamespace(
            dedicated_serving_endpoint=SimpleNamespace(
                public_endpoint_domain_name="fs.test.googleapis.com"
            )
        )
    )
    data_stub = _FakeDataClient({k: dict(v) for k, v in seeded.items()})
    return FeatureStoreOnlineClient(
        project_id="test-proj",
        location="europe-west4",
        admin_client=admin_stub,
        data_client_factory=lambda **_kw: data_stub,
    )


def test_feature_store_scalar_array_and_composite_keys(
    feature_store: fs_ports.FeatureOnlineStorePort,
) -> None:
    assert isinstance(feature_store, fs_ports.FeatureOnlineStorePort)
    res = feature_store.read_online_features(
        "store-1",
        "view-1",
        entity_ids=["tenant-a:sku-1", ["tenant-a", "sku-2"], "missing-id"],
    )
    assert res["tenant-a:sku-1"] == _FV_DATA
    assert res["tenant-a:sku-2"] == {"tier": "standard", "count": 7}
    assert res["missing-id"] == {}

    filtered = feature_store.read_online_features(
        "store-1",
        "view-1",
        entity_ids=["tenant-a:sku-1"],
        features=["flag", "counts"],
    )
    assert filtered == {"tenant-a:sku-1": {"flag": True, "counts": [1, 2, 3]}}


async def test_feature_store_empty_and_invalid_keys(
    feature_store: fs_ports.FeatureOnlineStorePort,
) -> None:
    assert (
        feature_store.read_online_features("store-1", "view-1", entity_ids=[])
        == {}
    )
    async_out = await feature_store.aread_online_features(
        "store-1", "view-1", entity_ids=["tenant-a:sku-2"]
    )
    assert async_out == {"tenant-a:sku-2": {"tier": "standard", "count": 7}}
    for bad_key in ("", "   ", [], ["tenant-a", ""]):
        with pytest.raises(exceptions.FeatureStoreError):
            feature_store.read_online_features(
                "store-1", "view-1", entity_ids=[bad_key]
            )
        with pytest.raises(exceptions.FeatureStoreError):
            await feature_store.aread_online_features(
                "store-1", "view-1", entity_ids=[bad_key]
            )


def _make_admin_client(calls: list[tuple[str, Any]]) -> SimpleNamespace:
    def _op(name: str) -> SimpleNamespace:
        return SimpleNamespace(result=lambda **_kw: SimpleNamespace(name=name))

    def _list_syncs(**kw: Any) -> list[SimpleNamespace]:
        calls.append(("list_syncs", kw["request"]))
        return [
            SimpleNamespace(
                name="sync-latest",
                run_time=SimpleNamespace(end_time=123),
                final_status=SimpleNamespace(code=0),
            )
        ]

    return SimpleNamespace(
        get_feature_online_store=lambda **_kw: (_ for _ in ()).throw(
            gcp_exceptions.NotFound("missing")
        ),
        create_feature_online_store=lambda **_kw: _op("store-created"),
        get_feature_view=lambda **_kw: SimpleNamespace(
            sync_config=SimpleNamespace(cron="0 * * * *")
        ),
        update_feature_view=lambda **kw: (
            calls.append(("update_view", kw)) or _op("view-updated")
        ),
        sync_feature_view=lambda **_kw: SimpleNamespace(
            feature_view_sync="sync-1"
        ),
        get_feature_view_sync=lambda **_kw: SimpleNamespace(
            run_time=SimpleNamespace(end_time=123),
            final_status=SimpleNamespace(code=0),
        ),
        list_feature_view_syncs=_list_syncs,
    )


def test_feature_store_admin_view_and_schedule_contract() -> None:
    calls: list[tuple[str, Any]] = []
    admin = FeatureStoreAdminClient(
        "test-proj", "europe-west4", admin_client=_make_admin_client(calls)
    )
    assert admin.ensure_online_store("store-1")["created"] is True
    view_res = admin.ensure_feature_view(
        "store-1", "view-1", bigquery_uri="test-proj.ds.tbl"
    )
    assert view_res == {"name": "view-updated", "created": False}
    assert admin.trigger_sync("store-1", "view-1")["sync_state"] == "SUCCEEDED"
    sched = admin.set_sync_schedule(
        "store-1", "view-1", cron_schedule="*/15 * * * *"
    )
    assert sched["cron_schedule"] == "*/15 * * * *"
    info = admin.get_sync_schedule("store-1", "view-1")
    assert info["cron_schedule"] == "0 * * * *"
    assert info["last_sync_state"] == "SUCCEEDED"
    list_reqs = [c[1] for c in calls if c[0] == "list_syncs"]
    assert list_reqs[0].order_by == "create_time desc"
