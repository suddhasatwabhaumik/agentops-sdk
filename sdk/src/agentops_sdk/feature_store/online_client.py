# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Low-latency Vertex AI Feature Online Store v2 data-plane client."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import threading
from typing import Any
import zlib

from google.api_core import client_options as api_client_options
from google.api_core import exceptions as gcp_exceptions
from google.cloud import aiplatform_v1

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import lifecycle
from agentops_sdk.feature_store import _protos
from agentops_sdk.ports import feature_store as fs_ports

_LOCK_STRIPES = 64
extract_feature_value = _protos.extract_feature_value


class FeatureStoreOnlineClient:
    """Vertex AI Feature Online Store v2 adapter with cached data clients."""

    def __init__(
        self,
        project_id: str | None = None,
        location: str | None = None,
        *,
        admin_client: Any | None = None,
        data_client_factory: Any | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        proj, loc = settings.resolve_gcp_project_and_location(
            project_id, location, require_project=True
        )
        self._project_id = str(proj)
        self._location = loc
        self._timeout = timeout_seconds
        self._admin_client = admin_client
        self._owns_admin = admin_client is None
        self._data_client_factory = data_client_factory
        self._owns_data_clients = data_client_factory is None
        self._lock = threading.Lock()
        self._stripes: tuple[threading.Lock, ...] = tuple(
            threading.Lock() for _ in range(_LOCK_STRIPES)
        )
        self._store_domains: lifecycle.BoundedTTLCache[str, str] = (
            lifecycle.BoundedTTLCache(maxsize=256, ttl_seconds=300.0)
        )
        self._data_clients: dict[str, Any] = {}

    def _get_store_lock(self, online_store_id: str) -> threading.Lock:
        idx = zlib.crc32(online_store_id.encode("utf-8")) % _LOCK_STRIPES
        return self._stripes[idx]

    def _get_admin_client(self) -> Any:
        with self._lock:
            if self._admin_client is None:
                opts = api_client_options.ClientOptions(
                    api_endpoint=f"{self._location}-aiplatform.googleapis.com"
                )
                self._admin_client = (
                    aiplatform_v1.FeatureOnlineStoreAdminServiceClient(
                        client_options=opts
                    )
                )
            return self._admin_client

    def _cached_client(self, online_store_id: str) -> Any | None:
        domain = self._store_domains.get(online_store_id)
        if domain is None:
            return None
        with self._lock:
            return self._data_clients.get(domain)

    def _resolve_store_domain(self, online_store_id: str) -> str:
        store_path = (
            f"projects/{self._project_id}/locations/{self._location}/"
            f"featureOnlineStores/{online_store_id}"
        )
        admin = self._get_admin_client()
        store = admin.get_feature_online_store(
            name=store_path, timeout=self._timeout
        )
        endpoint = getattr(store, "dedicated_serving_endpoint", None)
        pub_domain = str(
            getattr(endpoint, "public_endpoint_domain_name", "") or ""
        ).strip()
        return pub_domain or f"{self._location}-aiplatform.googleapis.com"

    def _get_data_client(self, online_store_id: str) -> Any:
        if (cached := self._cached_client(online_store_id)) is not None:
            return cached
        with self._get_store_lock(online_store_id):
            if (cached := self._cached_client(online_store_id)) is not None:
                return cached
            domain = self._store_domains.get(online_store_id)
            if domain is None:
                domain = self._resolve_store_domain(online_store_id)
                self._store_domains.set(online_store_id, domain)
            with self._lock:
                existing = self._data_clients.get(domain)
            if existing is not None:
                return existing
            opts = api_client_options.ClientOptions(api_endpoint=domain)
            factory = (
                self._data_client_factory
                or aiplatform_v1.FeatureOnlineStoreServiceClient
            )
            created = factory(client_options=opts)
            with self._lock:
                winner = self._data_clients.get(domain)
                if winner is None:
                    self._data_clients[domain] = created
                    return created
            if self._owns_data_clients:
                _protos.close_client_safely(created)
            return winner

    def _fetch_single_entity(
        self,
        data_client: Any,
        view_path: str,
        entity_key: fs_ports.EntityKey,
        feature_filter: set[str] | None,
    ) -> tuple[str, dict[str, Any]]:
        norm_key, data_key = _protos.build_data_key(entity_key)
        req = aiplatform_v1.FetchFeatureValuesRequest(
            feature_view=view_path,
            data_key=data_key,
        )
        try:
            resp = data_client.fetch_feature_values(
                request=req, timeout=self._timeout
            )
        except gcp_exceptions.NotFound:
            return norm_key, {}
        pairs = (
            getattr(getattr(resp, "key_values", None), "features", None) or ()
        )
        extracted: dict[str, Any] = {}
        for pair in pairs:
            fname = str(getattr(pair, "name", "") or "")
            if not fname or (
                feature_filter is not None and fname not in feature_filter
            ):
                continue
            val = extract_feature_value(getattr(pair, "value", None))
            if val is not None:
                extracted[fname] = val
        return norm_key, extracted

    def _read_entities(
        self,
        store_id: str,
        view_id: str,
        entity_ids: Sequence[fs_ports.EntityKey],
        feature_filter: set[str] | None,
    ) -> dict[str, dict[str, Any]]:
        view_path = (
            f"projects/{self._project_id}/locations/{self._location}/"
            f"featureOnlineStores/{store_id}/featureViews/{view_id}"
        )
        data_client = self._get_data_client(store_id)
        results: dict[str, dict[str, Any]] = {}
        for entity_key in entity_ids:
            norm_key, fmap = self._fetch_single_entity(
                data_client, view_path, entity_key, feature_filter
            )
            results[norm_key] = fmap
        return results

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
        if not entity_ids:
            return {}
        feature_filter = set(features) if features is not None else None
        try:
            return self._read_entities(
                store_id, view_id, entity_ids, feature_filter
            )
        except exceptions.FeatureStoreError:
            raise
        except (
            gcp_exceptions.GoogleAPICallError,
            OSError,
            TimeoutError,
        ) as exc:
            self._store_domains.pop(store_id)
            raise exceptions.FeatureStoreError(
                f"Failed reading online features from {store_id}/{view_id}: "
                f"{exc}"
            ) from exc
        except Exception as exc:
            raise exceptions.FeatureStoreError(
                f"Failed reading online features from {store_id}/{view_id}: "
                f"{exc}"
            ) from exc

    async def aread_online_features(
        self,
        online_store_id: str,
        feature_view_id: str,
        *,
        entity_ids: Sequence[fs_ports.EntityKey],
        features: Sequence[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Asynchronously reads typed feature maps keyed by entity ID."""
        return await asyncio.to_thread(
            self.read_online_features,
            online_store_id,
            feature_view_id,
            entity_ids=entity_ids,
            features=features,
        )

    def close(self) -> None:
        """Closes owned gRPC clients and clears cached store domains."""
        self._store_domains.clear()
        with self._lock:
            data_clients = list(self._data_clients.values())
            self._data_clients.clear()
            admin_cli = self._admin_client if self._owns_admin else None
            if self._owns_admin:
                self._admin_client = None
        if self._owns_data_clients:
            for client in data_clients:
                _protos.close_client_safely(client)
        _protos.close_client_safely(admin_cli)

    def __enter__(self) -> FeatureStoreOnlineClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
