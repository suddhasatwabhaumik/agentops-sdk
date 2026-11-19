# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Google Cloud Secret Manager adapter with bounded TTL singleflight cache."""

from __future__ import annotations

import collections
from collections.abc import Mapping
import contextlib
import re
import threading
from typing import Any
import zlib

from google.api_core import exceptions as gcp_exceptions
from google.cloud import secretmanager

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import lifecycle

_SECRET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,255}$")
_VERSION_RE = re.compile(r"^(?:latest|[0-9]{1,20})$")
_LOCK_STRIPES = 256


def validate_secret_id(secret_id: str) -> str:
    """Validates a Secret Manager identifier against path traversal."""
    if not isinstance(secret_id, str) or not _SECRET_ID_RE.match(secret_id):
        raise exceptions.SecretAccessError(
            f"Invalid secret_id {secret_id!r}; must match ^[A-Za-z0-9_-]+$."
        )
    return secret_id


def _validate_version(version: str) -> str:
    """Validates a secret version specifier ('latest' or positive integer)."""
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise exceptions.SecretAccessError(
            f"Invalid secret version {version!r}."
        )
    return version


class SecretManagerStore:
    """SecretStorePort adapter backed by Google Cloud Secret Manager."""

    def __init__(
        self,
        *,
        project_id: str | None = None,
        maxsize: int = 512,
        ttl_seconds: float = 300.0,
        timeout: float = 10.0,
        client: Any | None = None,
    ) -> None:
        proj, _ = settings.resolve_gcp_project_and_location(
            project_id, None, require_project=True
        )
        self._project_id = proj or ""
        self._maxsize = maxsize
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None
        self._client_lock = threading.Lock()
        self._cache: lifecycle.BoundedTTLCache[tuple[str, str, str], str] = (
            lifecycle.BoundedTTLCache(maxsize=maxsize, ttl_seconds=ttl_seconds)
        )
        self._stripes: tuple[threading.Lock, ...] = tuple(
            threading.Lock() for _ in range(_LOCK_STRIPES)
        )
        self._versions_lock = threading.Lock()
        self._tracked_versions: collections.OrderedDict[str, set[str]] = (
            collections.OrderedDict()
        )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is None:
                self._client = secretmanager.SecretManagerServiceClient()
            return self._client

    def _get_key_lock(self, key: tuple[str, str, str]) -> threading.Lock:
        raw = f"{key[0]}:{key[1]}".encode()
        return self._stripes[zlib.crc32(raw) % _LOCK_STRIPES]

    def _record_cached_version(self, clean_id: str, clean_ver: str) -> None:
        with self._versions_lock:
            vers = self._tracked_versions.get(clean_id)
            if vers is None:
                vers = set()
                self._tracked_versions[clean_id] = vers
            else:
                self._tracked_versions.move_to_end(clean_id)
            vers.add(clean_ver)
            while len(self._tracked_versions) > max(self._maxsize * 2, 16):
                self._tracked_versions.popitem(last=False)

    def _evict_secret_versions(self, clean_id: str) -> None:
        with self._versions_lock:
            tracked = self._tracked_versions.pop(clean_id, set())
        for ver in tracked | {"latest"}:
            self._cache.pop((self._project_id, clean_id, ver))

    def get_secret(self, secret_id: str, *, version: str = "latest") -> str:
        """Fetches a secret version with singleflight caching and revocation."""
        clean_id = validate_secret_id(secret_id)
        clean_ver = _validate_version(version)
        cache_key = (self._project_id, clean_id, clean_ver)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        with self._get_key_lock(cache_key):
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
            name = (
                f"projects/{self._project_id}/secrets/{clean_id}"
                f"/versions/{clean_ver}"
            )
            try:
                resp = self._get_client().access_secret_version(
                    request={"name": name}, timeout=self._timeout
                )
                value = str(resp.payload.data.decode("utf-8"))
            except (
                gcp_exceptions.PermissionDenied,
                gcp_exceptions.NotFound,
            ) as exc:
                self._evict_secret_versions(clean_id)
                raise exceptions.SecretAccessError(
                    f"Secret {clean_id!r} was revoked or not found."
                ) from exc
            except Exception as exc:
                raise exceptions.SecretAccessError(
                    f"Failed to retrieve secret {clean_id!r}."
                ) from exc
            self._record_cached_version(clean_id, clean_ver)
            self._cache.set(cache_key, value)
            return value

    def _add_or_create_version(self, clean_id: str, raw_bytes: bytes) -> Any:
        client = self._get_client()
        parent = f"projects/{self._project_id}"
        secret_path = f"{parent}/secrets/{clean_id}"
        req = {"parent": secret_path, "payload": {"data": raw_bytes}}
        try:
            return client.add_secret_version(request=req, timeout=self._timeout)
        except gcp_exceptions.NotFound:
            client.create_secret(
                request={
                    "parent": parent,
                    "secret_id": clean_id,
                    "secret": {"replication": {"automatic": {}}},
                },
                timeout=self._timeout,
            )
            return client.add_secret_version(request=req, timeout=self._timeout)

    def put_secret(self, secret_id: str, payload: str) -> str:
        """Creates or appends a new secret version in Secret Manager."""
        clean_id = validate_secret_id(secret_id)
        latest_key = (self._project_id, clean_id, "latest")
        raw_bytes = payload.encode("utf-8")
        with self._get_key_lock(latest_key):
            try:
                resp = self._add_or_create_version(clean_id, raw_bytes)
            except Exception as exc:
                self._cache.pop(latest_key)
                raise exceptions.SecretAccessError(
                    f"Failed to store secret {clean_id!r}."
                ) from exc
            self._record_cached_version(clean_id, "latest")
            self._cache.set(latest_key, payload)
            return str(resp.name)

    def delete_secret(self, secret_id: str) -> bool:
        """Deletes a secret from Secret Manager and evicts cached versions."""
        clean_id = validate_secret_id(secret_id)
        latest_key = (self._project_id, clean_id, "latest")
        secret_path = f"projects/{self._project_id}/secrets/{clean_id}"
        with self._get_key_lock(latest_key):
            self._evict_secret_versions(clean_id)
            try:
                self._get_client().delete_secret(
                    request={"name": secret_path}, timeout=self._timeout
                )
                return True
            except gcp_exceptions.NotFound:
                return False
            except Exception as exc:
                raise exceptions.SecretAccessError(
                    f"Failed to delete secret {clean_id!r}."
                ) from exc

    def evict(self, secret_id: str, *, version: str = "latest") -> None:
        """Explicitly evicts a cached secret entry."""
        clean_id = validate_secret_id(secret_id)
        clean_ver = _validate_version(version)
        self._cache.pop((self._project_id, clean_id, clean_ver))

    def close(self) -> None:
        """Closes owned Secret Manager client and clears cached secrets."""
        with self._client_lock:
            cli = self._client if self._owns_client else None
            if self._owns_client:
                self._client = None
        self._cache.clear()
        with self._versions_lock:
            self._tracked_versions.clear()
        if cli is not None:
            transport = getattr(cli, "transport", None)
            close_fn = getattr(cli, "close", None) or getattr(
                transport, "close", None
            )
            if callable(close_fn):
                with contextlib.suppress(Exception):
                    close_fn()

    def __enter__(self) -> SecretManagerStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class InMemorySecretStore:
    """Deterministic zero-dependency in-memory SecretStorePort adapter."""

    def __init__(
        self,
        initial_secrets: Mapping[str, str] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, list[str]] = {}
        if initial_secrets:
            for key, val in initial_secrets.items():
                self.put_secret(key, val)

    def get_secret(self, secret_id: str, *, version: str = "latest") -> str:
        """Returns secret plaintext by secret_id and version."""
        clean_id = validate_secret_id(secret_id)
        clean_ver = _validate_version(version)
        with self._lock:
            versions = self._store.get(clean_id)
            if not versions:
                raise exceptions.SecretAccessError(
                    f"Secret {clean_id!r} not found in memory store."
                )
            if clean_ver == "latest":
                return versions[-1]
            idx = int(clean_ver) - 1
            if idx < 0 or idx >= len(versions):
                raise exceptions.SecretAccessError(
                    f"Version {clean_ver!r} not found for secret {clean_id!r}."
                )
            return versions[idx]

    def put_secret(self, secret_id: str, payload: str) -> str:
        """Appends a new in-memory secret version."""
        clean_id = validate_secret_id(secret_id)
        with self._lock:
            versions = self._store.setdefault(clean_id, [])
            versions.append(payload)
            ver_num = len(versions)
        return f"memory/secrets/{clean_id}/versions/{ver_num}"

    def delete_secret(self, secret_id: str) -> bool:
        """Deletes secret_id from memory and returns True if present."""
        clean_id = validate_secret_id(secret_id)
        with self._lock:
            return self._store.pop(clean_id, None) is not None
