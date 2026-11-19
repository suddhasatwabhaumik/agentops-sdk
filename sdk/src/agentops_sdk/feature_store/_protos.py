# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Protobuf builders, sync polling, and FeatureValue extraction helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import contextlib
import time
from typing import Any, Literal

from google.cloud import aiplatform_v1
from google.protobuf import json_format

from agentops_sdk.domain import exceptions
from agentops_sdk.feature_store import in_memory
from agentops_sdk.ports import feature_store as fs_ports

_SCALAR_KINDS = frozenset(
    {
        "bool_value",
        "int64_value",
        "double_value",
        "string_value",
        "bytes_value",
    }
)
_ARRAY_KINDS = frozenset(
    {
        "int64_array_value",
        "double_array_value",
        "string_array_value",
        "bool_array_value",
    }
)
_MAX_STRUCT_DEPTH = 16


def close_client_safely(client: Any) -> None:
    """Closes a gRPC client or its underlying transport, ignoring errors."""
    if client is None:
        return
    transport = getattr(client, "transport", None)
    close_fn = getattr(client, "close", None) or getattr(
        transport, "close", None
    )
    if callable(close_fn):
        with contextlib.suppress(Exception):
            close_fn()


def _extract_struct_value(struct_obj: Any, *, depth: int) -> Any:
    fields = getattr(struct_obj, "values", None)
    if fields is not None and not callable(fields):
        return {
            str(item.name): extract_feature_value(
                getattr(item, "value", None), _depth=depth + 1
            )
            for item in fields
            if getattr(item, "name", "")
        }
    return json_format.MessageToDict(struct_obj)


def extract_feature_value(feat_val: Any, *, _depth: int = 0) -> Any:
    """Extracts a typed Python value from FeatureValue via WhichOneof."""
    if feat_val is None or _depth > _MAX_STRUCT_DEPTH:
        return None
    pb = getattr(feat_val, "_pb", feat_val)
    which_fn = getattr(pb, "WhichOneof", None)
    if not callable(which_fn):
        return None
    kind = which_fn("value")
    if kind is None:
        return None
    if kind in _SCALAR_KINDS:
        return getattr(pb, kind)
    if kind in _ARRAY_KINDS:
        return list(getattr(pb, kind).values)
    if kind == "struct_value":
        return _extract_struct_value(getattr(pb, kind), depth=_depth)
    return None


def build_data_key(
    entity_key: fs_ports.EntityKey,
) -> tuple[str, aiplatform_v1.FeatureViewDataKey]:
    """Builds canonical string key and FeatureViewDataKey protobuf."""
    norm_key = in_memory.normalize_entity_key(entity_key)
    if isinstance(entity_key, str):
        return norm_key, aiplatform_v1.FeatureViewDataKey(key=norm_key)
    parts = [str(part).strip() for part in entity_key]
    comp = aiplatform_v1.FeatureViewDataKey.CompositeKey(parts=parts)
    return norm_key, aiplatform_v1.FeatureViewDataKey(composite_key=comp)


def resolve_sync_state(
    sync_obj: Any,
) -> Literal["SUCCEEDED", "FAILED", "RUNNING"]:
    """Determines sync terminal state from run_time.end_time and status code."""
    rt = getattr(sync_obj, "run_time", None)
    if rt is None:
        return "RUNNING"
    if callable(has_fn := getattr(rt, "HasField", None)):
        ended = bool(has_fn("end_time"))
    elif (et := getattr(rt, "end_time", None)) is None:
        ended = False
    elif isinstance(secs := getattr(et, "seconds", None), int):
        ended = secs > 0 or int(getattr(et, "nanos", 0) or 0) > 0
    else:
        ended = bool(et)
    if not ended:
        return "RUNNING"
    status = getattr(sync_obj, "final_status", None)
    return (
        "SUCCEEDED" if int(getattr(status, "code", 0) or 0) == 0 else "FAILED"
    )


def build_store_proto(
    store_type: Literal["bigtable", "optimized"],
    min_nodes: int,
    max_nodes: int,
) -> aiplatform_v1.FeatureOnlineStore:
    """Constructs a Bigtable or Optimized FeatureOnlineStore protobuf."""
    if store_type == "bigtable":
        if min_nodes < 1 or max_nodes < min_nodes:
            raise ValueError("Require 1 <= min_nodes <= max_nodes.")
        scale = aiplatform_v1.FeatureOnlineStore.Bigtable.AutoScaling(
            min_node_count=min_nodes, max_node_count=max_nodes
        )
        return aiplatform_v1.FeatureOnlineStore(
            bigtable=aiplatform_v1.FeatureOnlineStore.Bigtable(
                auto_scaling=scale
            )
        )
    if store_type == "optimized":
        return aiplatform_v1.FeatureOnlineStore(
            optimized=aiplatform_v1.FeatureOnlineStore.Optimized(),
            dedicated_serving_endpoint=(
                aiplatform_v1.FeatureOnlineStore.DedicatedServingEndpoint()
            ),
        )
    raise ValueError(f"Unsupported store_type: {store_type!r}")


def build_view_proto(
    vpath: str,
    bigquery_uri: str,
    entity_cols: Sequence[str],
    cron: str | None,
    index_cfg: Mapping[str, Any] | None,
) -> tuple[aiplatform_v1.FeatureView, list[str]]:
    """Constructs a FeatureView protobuf and corresponding FieldMask paths."""
    raw = bigquery_uri.strip()
    if not raw:
        raise ValueError("bigquery_uri must be non-empty.")
    uri = raw if raw.startswith("bq://") else f"bq://{raw}"
    bq_src = aiplatform_v1.FeatureView.BigQuerySource(
        uri=uri, entity_id_columns=list(entity_cols)
    )
    paths = ["big_query_source"]
    sync_cfg = (
        aiplatform_v1.FeatureView.SyncConfig(cron=cron)
        if cron is not None
        else None
    )
    if sync_cfg is not None:
        paths.append("sync_config")
    idx = (
        aiplatform_v1.FeatureView.IndexConfig(**dict(index_cfg))
        if index_cfg
        else None
    )
    if index_cfg is not None:
        paths.append("index_config")
    view = aiplatform_v1.FeatureView(
        name=vpath,
        big_query_source=bq_src,
        sync_config=sync_cfg,
        index_config=idx,
    )
    return view, paths


def poll_sync(
    client: Any, sname: str, timeout: float, interval: float
) -> dict[str, str]:
    """Polls get_feature_view_sync until completion or timeout."""
    deadline = time.monotonic() + max(0.1, timeout)
    while True:
        state = resolve_sync_state(client.get_feature_view_sync(name=sname))
        if state in {"SUCCEEDED", "FAILED"}:
            return {"sync_name": sname, "sync_state": state}
        if (rem := deadline - time.monotonic()) <= 0.0:
            raise exceptions.FeatureStoreError(f"Sync timeout: {sname!r}.")
        time.sleep(min(max(0.05, interval), rem))
