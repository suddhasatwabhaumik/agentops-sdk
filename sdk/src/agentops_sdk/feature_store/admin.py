# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Control-plane client for Vertex AI Feature Online Store provisioning/sync."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import threading
from typing import Any, Literal

from google.api_core import client_options as api_client_options
from google.api_core import exceptions as gcp_exceptions
from google.cloud import aiplatform_v1
from google.protobuf import field_mask_pb2

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.feature_store import _protos


class FeatureStoreAdminClient:
    """Control-plane client for Feature Online Store provisioning and sync."""

    def __init__(
        self,
        project_id: str | None = None,
        location: str | None = None,
        *,
        admin_client: Any | None = None,
    ) -> None:
        """Initializes FeatureStoreAdminClient for a GCP project and region."""
        proj, loc = settings.resolve_gcp_project_and_location(
            project_id, location, require_project=True
        )
        self._project_id = str(proj)
        self._location = loc
        self._admin_client = admin_client
        self._owns_client = admin_client is None
        self._lock = threading.Lock()

    def _get_client(self) -> Any:
        with self._lock:
            if self._admin_client is None:
                ep = f"{self._location}-aiplatform.googleapis.com"
                opts = api_client_options.ClientOptions(api_endpoint=ep)
                cls = aiplatform_v1.FeatureOnlineStoreAdminServiceClient
                self._admin_client = cls(client_options=opts)
            return self._admin_client

    def _store_path(self, store_id: str) -> str:
        cid = settings.validate_gcp_identifier(store_id, "online_store_id")
        return (
            f"projects/{self._project_id}/locations/{self._location}/"
            f"featureOnlineStores/{cid}"
        )

    def _view_path(self, store_id: str, view_id: str) -> str:
        vid = settings.validate_gcp_identifier(view_id, "feature_view_id")
        return f"{self._store_path(store_id)}/featureViews/{vid}"

    def _create_online_store(
        self,
        cid: str,
        spath: str,
        proto: aiplatform_v1.FeatureOnlineStore,
        store_type: Literal["bigtable", "optimized"],
        wait_for_completion: bool,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        parent = f"projects/{self._project_id}/locations/{self._location}"
        try:
            op = self._get_client().create_feature_online_store(
                parent=parent,
                feature_online_store=proto,
                feature_online_store_id=cid,
            )
            if wait_for_completion:
                spath = str(
                    getattr(op.result(timeout=timeout_seconds), "name", spath)
                )
            return {"name": spath, "store_type": store_type, "created": True}
        except Exception as exc:
            raise exceptions.FeatureStoreError(
                f"Create {cid!r}: {exc}"
            ) from exc

    def ensure_online_store(
        self,
        online_store_id: str,
        *,
        store_type: Literal["bigtable", "optimized"] = "bigtable",
        min_nodes: int = 1,
        max_nodes: int = 1,
        wait_for_completion: bool = True,
        timeout_seconds: float = 900.0,
    ) -> dict[str, Any]:
        """Provisions or verifies a Bigtable or Optimized FeatureOnlineStore."""
        cid = settings.validate_gcp_identifier(
            online_store_id, "online_store_id"
        )
        proto = _protos.build_store_proto(store_type, min_nodes, max_nodes)
        spath = self._store_path(cid)
        try:
            found = self._get_client().get_feature_online_store(name=spath)
            name = str(getattr(found, "name", spath))
            return {"name": name, "store_type": store_type, "created": False}
        except gcp_exceptions.NotFound:
            pass
        except Exception as exc:
            raise exceptions.FeatureStoreError(
                f"Inspect {cid!r}: {exc}"
            ) from exc
        return self._create_online_store(
            cid, spath, proto, store_type, wait_for_completion, timeout_seconds
        )

    def _upsert_feature_view(
        self,
        spath: str,
        vpath: str,
        vid: str,
        proto: aiplatform_v1.FeatureView,
        paths: list[str],
        wait_for_completion: bool,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        client = self._get_client()
        exists = self._view_exists(client, vpath, vid)
        try:
            mask = field_mask_pb2.FieldMask(paths=paths)
            op = (
                client.update_feature_view(feature_view=proto, update_mask=mask)
                if exists
                else client.create_feature_view(
                    parent=spath, feature_view=proto, feature_view_id=vid
                )
            )
            if wait_for_completion:
                vpath = str(
                    getattr(op.result(timeout=timeout_seconds), "name", vpath)
                )
            return {"name": vpath, "created": not exists}
        except Exception as exc:
            raise exceptions.FeatureStoreError(
                f"Ensure {vid!r}: {exc}"
            ) from exc

    def ensure_feature_view(
        self,
        online_store_id: str,
        feature_view_id: str,
        *,
        bigquery_uri: str,
        entity_id_columns: Sequence[str] = ("entity_id",),
        cron_schedule: str | None = None,
        index_config: Mapping[str, Any] | None = None,
        wait_for_completion: bool = True,
        timeout_seconds: float = 900.0,
    ) -> dict[str, Any]:
        """Provisions or updates a FeatureView and optionally awaits LRO."""
        vid = settings.validate_gcp_identifier(
            feature_view_id, "feature_view_id"
        )
        spath = self._store_path(online_store_id)
        vpath = self._view_path(online_store_id, vid)
        proto, paths = _protos.build_view_proto(
            vpath, bigquery_uri, entity_id_columns, cron_schedule, index_config
        )
        return self._upsert_feature_view(
            spath,
            vpath,
            vid,
            proto,
            paths,
            wait_for_completion,
            timeout_seconds,
        )

    def _view_exists(self, client: Any, vpath: str, vid: str) -> bool:
        try:
            client.get_feature_view(name=vpath)
            return True
        except gcp_exceptions.NotFound:
            return False
        except Exception as exc:
            raise exceptions.FeatureStoreError(f"Check {vid!r}: {exc}") from exc

    def trigger_sync(
        self,
        online_store_id: str,
        feature_view_id: str,
        *,
        wait_for_completion: bool = True,
        timeout_seconds: float = 600.0,
        poll_interval_seconds: float = 5.0,
    ) -> dict[str, str]:
        """Triggers FeatureView sync and polls run_time.end_time & code."""
        vpath = self._view_path(online_store_id, feature_view_id)
        client = self._get_client()
        try:
            resp = client.sync_feature_view(feature_view=vpath)
            sname = str(getattr(resp, "feature_view_sync", ""))
            if not wait_for_completion:
                return {"sync_name": sname, "sync_state": "RUNNING"}
            return _protos.poll_sync(
                client, sname, timeout_seconds, poll_interval_seconds
            )
        except exceptions.FeatureStoreError:
            raise
        except Exception as exc:
            raise exceptions.FeatureStoreError(
                f"Sync {feature_view_id!r}: {exc}"
            ) from exc

    def set_sync_schedule(
        self,
        online_store_id: str,
        feature_view_id: str,
        *,
        cron_schedule: str | None = None,
        wait_for_completion: bool = True,
        timeout_seconds: float = 300.0,
    ) -> dict[str, Any]:
        """Configures or clears the in-engine cron schedule on FeatureView."""
        vpath = self._view_path(online_store_id, feature_view_id)
        cron = (cron_schedule or "").strip()
        view = aiplatform_v1.FeatureView(
            name=vpath,
            sync_config=aiplatform_v1.FeatureView.SyncConfig(cron=cron),
        )
        mask = field_mask_pb2.FieldMask(paths=["sync_config.cron"])
        try:
            op = self._get_client().update_feature_view(
                feature_view=view, update_mask=mask
            )
            if wait_for_completion:
                op.result(timeout=timeout_seconds)
            return {"feature_view": vpath, "cron_schedule": cron or None}
        except Exception as exc:
            raise exceptions.FeatureStoreError(
                f"Sched {feature_view_id!r}: {exc}"
            ) from exc

    def get_sync_schedule(
        self, online_store_id: str, feature_view_id: str
    ) -> dict[str, Any]:
        """Reads active cron schedule and latest sync status."""
        vpath = self._view_path(online_store_id, feature_view_id)
        client = self._get_client()
        try:
            fv = client.get_feature_view(name=vpath)
            cron = str(
                getattr(getattr(fv, "sync_config", None), "cron", "") or ""
            )
            req = aiplatform_v1.ListFeatureViewSyncsRequest(
                parent=vpath, order_by="create_time desc", page_size=1
            )
            latest = next(
                iter(client.list_feature_view_syncs(request=req)), None
            )
            return {
                "feature_view": vpath,
                "cron_schedule": cron or None,
                "last_sync_name": str(latest.name)
                if latest is not None
                else None,
                "last_sync_state": (
                    _protos.resolve_sync_state(latest) if latest else None
                ),
            }
        except Exception as exc:
            raise exceptions.FeatureStoreError(
                f"Read {feature_view_id!r}: {exc}"
            ) from exc

    def close(self) -> None:
        """Closes owned Feature Store admin client transport if created."""
        with self._lock:
            cli = self._admin_client if self._owns_client else None
            if self._owns_client:
                self._admin_client = None
        _protos.close_client_safely(cli)

    def __enter__(self) -> FeatureStoreAdminClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
