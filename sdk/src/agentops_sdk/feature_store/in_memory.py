# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Deterministic thread-safe in-memory FeatureOnlineStorePort adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
import threading
from typing import Any

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.ports import feature_store as fs_ports


def normalize_entity_key(entity_key: fs_ports.EntityKey) -> str:
    """Converts a single or composite entity key into a canonical string key."""
    if isinstance(entity_key, str):
        cleaned = entity_key.strip()
        if not cleaned:
            raise exceptions.FeatureStoreError(
                "Entity key must be a non-empty string."
            )
        return cleaned
    parts = [str(part).strip() for part in entity_key]
    if not parts or any(not p for p in parts):
        raise exceptions.FeatureStoreError(
            "Composite entity key must contain non-empty string parts."
        )
    return ":".join(parts)


class InMemoryFeatureOnlineStore:
    """Thread-safe in-memory adapter implementing FeatureOnlineStorePort."""

    def __init__(self) -> None:
        """Initializes an empty thread-safe InMemoryFeatureOnlineStore."""
        self._lock = threading.RLock()
        self._store: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}

    def seed_features(
        self,
        online_store_id: str,
        feature_view_id: str,
        features_by_entity: (
            Mapping[str, Mapping[str, Any]]
            | Mapping[Sequence[str], Mapping[str, Any]]
            | Mapping[fs_ports.EntityKey, Mapping[str, Any]]
        ),
    ) -> None:
        """Seeds feature vectors for testing and local verification."""
        store_id = settings.validate_gcp_identifier(
            online_store_id, "online_store_id"
        )
        view_id = settings.validate_gcp_identifier(
            feature_view_id, "feature_view_id"
        )
        with self._lock:
            view_bucket = self._store.setdefault((store_id, view_id), {})
            for raw_key, feat_map in features_by_entity.items():
                norm_key = normalize_entity_key(raw_key)
                view_bucket[norm_key] = copy.deepcopy(dict(feat_map))

    def clear(self) -> None:
        """Clears all seeded online stores and feature views."""
        with self._lock:
            self._store.clear()

    def read_online_features(
        self,
        online_store_id: str,
        feature_view_id: str,
        *,
        entity_ids: Sequence[fs_ports.EntityKey],
        features: Sequence[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Reads typed feature maps keyed by stringified entity ID."""
        store_id = settings.validate_gcp_identifier(
            online_store_id, "online_store_id"
        )
        view_id = settings.validate_gcp_identifier(
            feature_view_id, "feature_view_id"
        )
        feature_filter = set(features) if features is not None else None
        with self._lock:
            view_bucket = self._store.get((store_id, view_id), {})
            results: dict[str, dict[str, Any]] = {}
            for raw_key in entity_ids:
                norm_key = normalize_entity_key(raw_key)
                stored = view_bucket.get(norm_key, {})
                results[norm_key] = {
                    fname: copy.deepcopy(fval)
                    for fname, fval in stored.items()
                    if feature_filter is None or fname in feature_filter
                }
            return results

    async def aread_online_features(
        self,
        online_store_id: str,
        feature_view_id: str,
        *,
        entity_ids: Sequence[fs_ports.EntityKey],
        features: Sequence[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Asynchronously reads typed feature maps keyed by entity ID."""
        return self.read_online_features(
            online_store_id,
            feature_view_id,
            entity_ids=entity_ids,
            features=features,
        )
