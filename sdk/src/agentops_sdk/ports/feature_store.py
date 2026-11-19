# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Port contract for low-latency online feature store lookups."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, TypeAlias, runtime_checkable

EntityKey: TypeAlias = str | Sequence[str]


@runtime_checkable
class FeatureOnlineStorePort(Protocol):
    """Port contract for reading online feature vectors by entity key."""

    def read_online_features(
        self,
        online_store_id: str,
        feature_view_id: str,
        *,
        entity_ids: Sequence[EntityKey],
        features: Sequence[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Reads typed feature maps keyed by stringified entity ID."""
        ...

    async def aread_online_features(
        self,
        online_store_id: str,
        feature_view_id: str,
        *,
        entity_ids: Sequence[EntityKey],
        features: Sequence[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Asynchronously reads typed feature maps keyed by entity ID."""
        ...
