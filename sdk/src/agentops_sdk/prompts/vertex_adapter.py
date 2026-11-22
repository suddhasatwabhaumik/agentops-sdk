# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Vertex AI Prompt Management adapter via client-scoped vertexai.Client."""

from __future__ import annotations

import contextlib
import inspect
import threading
from typing import Any

from google.api_core import exceptions as gcp_exceptions
from google.genai import errors as genai_errors
import vertexai
from vertexai._genai import types as vertex_types

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.ports import prompts as prompt_ports
from agentops_sdk.prompts import _vertex_codec as codec
from agentops_sdk.prompts import local_yaml_adapter as yaml_utils


class VertexAIPromptAdapter:
    """Vertex AI Prompt Management adapter implementing PromptBackendPort."""

    def __init__(
        self,
        project_id: str | None = None,
        location: str | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        """Initializes VertexAIPromptAdapter for a GCP project and region."""
        proj, loc = settings.resolve_gcp_project_and_location(
            project_id, location, require_project=client is None
        )
        self._project_id, self._location = proj, loc
        self._owns_client = client is None
        self._lock = threading.RLock()
        self._id_cache: dict[str, str] = {}
        try:
            self._client = client or vertexai.Client(project=proj, location=loc)
        except Exception as exc:
            raise exceptions.PromptRegistryError(
                f"Vertex init failed: {exc}"
            ) from exc

    def close(self) -> None:
        """Clears cached IDs and closes an internally managed Vertex client."""
        with self._lock:
            self._id_cache.clear()
        if not self._owns_client:
            return
        for obj in (self._client, getattr(self._client, "_api_client", None)):
            if callable(close_fn := getattr(obj, "close", None)):
                with contextlib.suppress(Exception):
                    close_fn()
                break

    def _cache_id(self, logical_id: str, dataset_id: str) -> str:
        with self._lock:
            self._id_cache[logical_id] = dataset_id
        return dataset_id

    def _evict_id(self, logical_id: str) -> None:
        with self._lock:
            self._id_cache.pop(logical_id, None)

    def _resolve_dataset_id(self, prompt_id: str) -> str | None:
        with self._lock:
            if prompt_id in self._id_cache:
                return self._id_cache[prompt_id]
        for ref in self._client.prompts.list():
            rid = str(getattr(ref, "prompt_id", "") or "")
            if not rid:
                continue
            if rid == prompt_id:
                return self._cache_id(prompt_id, rid)
            try:
                ds = getattr(
                    self._client.prompts.get(prompt_id=rid), "dataset", None
                )
            except (gcp_exceptions.NotFound, genai_errors.APIError) as exc:
                if codec.is_not_found(exc):
                    continue
                raise
            if getattr(ds, "display_name", None) == prompt_id:
                return self._cache_id(prompt_id, rid)
        return None

    def _version_entries(self, ds_id: str) -> list[tuple[str, str]]:
        prompts_api = self._client.prompts
        pager = inspect.getattr_static(
            prompts_api, "_list_versions_pager", None
        )
        entries: list[tuple[str, str]] = []
        if pager is not None:
            for dv in prompts_api._list_versions_pager(prompt_id=ds_id):
                raw = str(getattr(dv, "name", "") or "")
                num = raw.rsplit("/", 1)[-1] if raw else ""
                num = num or str(getattr(dv, "version_id", "") or "")
                disp = getattr(dv, "display_name", None)
                if num:
                    entries.append((str(disp or num), num))
            return entries
        for ref in prompts_api.list_versions(prompt_id=ds_id):
            vid = getattr(ref, "version_id", None)
            disp = getattr(ref, "display_name", None)
            if vid:
                entries.append((str(disp or vid), str(vid)))
        return entries

    def _fetch_prompt_version(
        self, cid: str, ds_id: str, version: str
    ) -> prompt_ports.PromptTemplate:
        ver_map = dict(self._version_entries(ds_id))
        if version != "latest":
            cver = yaml_utils.validate_version_id(version)
            vp = self._client.prompts.get_version(
                prompt_id=ds_id, version_id=ver_map.get(cver, cver)
            )
            return codec.to_domain_template(vp, cid, cver)
        vers = yaml_utils.sort_versions(ver_map)
        vp = (
            self._client.prompts.get_version(
                prompt_id=ds_id, version_id=ver_map[vers[-1]]
            )
            if vers
            else self._client.prompts.get(prompt_id=ds_id)
        )
        return codec.to_domain_template(vp, cid, vers[-1] if vers else "1.0.0")

    def get_prompt(
        self, prompt_id: str, version: str = "latest"
    ) -> prompt_ports.PromptTemplate:
        """Fetches a PromptTemplate from Vertex AI by ID and version."""
        cid = yaml_utils.validate_prompt_id(prompt_id)
        try:
            ds_id = self._resolve_dataset_id(cid)
            if ds_id is None:
                raise exceptions.PromptNotFoundError(f"Prompt {cid!r} missing.")
            return self._fetch_prompt_version(cid, ds_id, version)
        except exceptions.PromptRegistryError:
            raise
        except (gcp_exceptions.NotFound, genai_errors.APIError) as exc:
            if codec.is_not_found(exc):
                self._evict_id(cid)
                raise exceptions.PromptNotFoundError(
                    f"Missing {cid!r}@{version!r}."
                ) from exc
            raise exceptions.PromptRegistryError(
                f"Vertex get {cid!r}: {exc}"
            ) from exc
        except Exception as exc:
            raise exceptions.PromptRegistryError(
                f"Vertex get {cid!r}: {exc}"
            ) from exc

    def register_prompt(
        self, prompt: prompt_ports.PromptTemplate
    ) -> prompt_ports.PromptTemplate:
        """Creates or updates a versioned prompt in Vertex AI."""
        cid = yaml_utils.validate_prompt_id(prompt.prompt_id)
        cver = yaml_utils.validate_version_id(prompt.version)
        vp_in = codec.build_vertex_prompt(prompt)
        try:
            ds_id = self._resolve_dataset_id(cid)
            saved = self._save_prompt(cid, cver, ds_id, vp_in)
            if getattr(saved, "prompt_id", None):
                self._cache_id(cid, str(saved.prompt_id))
            return codec.to_domain_template(
                saved, cid, cver, prompt.description, prompt.metadata
            )
        except Exception as exc:
            raise exceptions.PromptRegistryError(
                f"Vertex save {cid!r}: {exc}"
            ) from exc

    def _save_prompt(
        self, cid: str, cver: str, ds_id: str | None, vp_in: vertex_types.Prompt
    ) -> Any:
        if ds_id is not None:
            try:
                ucfg = vertex_types.UpdatePromptConfig(
                    prompt_display_name=cid, version_display_name=cver
                )
                return self._client.prompts.update(
                    prompt_id=ds_id, prompt=vp_in, config=ucfg
                )
            except (gcp_exceptions.NotFound, genai_errors.APIError) as exc:
                if not codec.is_not_found(exc):
                    raise
                self._evict_id(cid)
        ccfg = vertex_types.CreatePromptVersionConfig(
            prompt_display_name=cid, version_display_name=cver
        )
        return self._client.prompts.create_version(prompt=vp_in, config=ccfg)

    def list_prompts(self) -> list[str]:
        """Lists sorted logical prompt display names or IDs in Vertex AI."""
        try:
            found: set[str] = set()
            for ref in self._client.prompts.list():
                rid = str(getattr(ref, "prompt_id", "") or "")
                if not rid:
                    continue
                ds = getattr(
                    self._client.prompts.get(prompt_id=rid), "dataset", None
                )
                cand = str(getattr(ds, "display_name", None) or rid)
                try:
                    valid = yaml_utils.validate_prompt_id(cand)
                    self._cache_id(valid, rid)
                    found.add(valid)
                except ValueError:
                    found.add(rid)
            return sorted(found)
        except Exception as exc:
            raise exceptions.PromptRegistryError(
                f"Vertex list failed: {exc}"
            ) from exc

    def list_versions(self, prompt_id: str) -> list[str]:
        """Returns SemVer-sorted version IDs for prompt_id in Vertex AI."""
        cid = yaml_utils.validate_prompt_id(prompt_id)
        try:
            ds_id = self._resolve_dataset_id(cid)
            if ds_id is None:
                raise exceptions.PromptNotFoundError(f"Prompt {cid!r} missing.")
            entries = self._version_entries(ds_id)
            return yaml_utils.sort_versions(logical for logical, _ in entries)
        except exceptions.PromptNotFoundError:
            raise
        except (gcp_exceptions.NotFound, genai_errors.APIError) as exc:
            if codec.is_not_found(exc):
                self._evict_id(cid)
                raise exceptions.PromptNotFoundError(
                    f"Prompt {cid!r} missing."
                ) from exc
            raise exceptions.PromptRegistryError(
                f"Vertex vers {cid!r}: {exc}"
            ) from exc
        except Exception as exc:
            raise exceptions.PromptRegistryError(
                f"Vertex vers {cid!r}: {exc}"
            ) from exc

    def delete_prompt(self, prompt_id: str, version: str | None = None) -> bool:
        """Deletes a prompt or specific version from Vertex AI."""
        cid = yaml_utils.validate_prompt_id(prompt_id)
        try:
            ds_id = self._resolve_dataset_id(cid)
            if ds_id is None:
                return False
            if version is None:
                self._client.prompts.delete(prompt_id=ds_id)
                self._evict_id(cid)
            else:
                cver = yaml_utils.validate_version_id(version)
                ver_map = dict(self._version_entries(ds_id))
                self._client.prompts.delete_version(
                    prompt_id=ds_id, version_id=ver_map.get(cver, cver)
                )
            return True
        except (gcp_exceptions.NotFound, genai_errors.APIError) as exc:
            if codec.is_not_found(exc):
                self._evict_id(cid)
                return False
            raise exceptions.PromptRegistryError(
                f"Vertex del {cid!r}: {exc}"
            ) from exc
        except Exception as exc:
            raise exceptions.PromptRegistryError(
                f"Vertex del {cid!r}: {exc}"
            ) from exc
