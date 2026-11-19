# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Google Cloud Storage versioned prompt adapter."""

from __future__ import annotations

from typing import Any

from google.api_core import exceptions as gcp_exceptions
from google.api_core import retry as gcp_retry
from google.cloud import storage
import pydantic
import yaml

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.ports import prompts as prompt_ports
from agentops_sdk.prompts import local_yaml_adapter as yaml_utils

_DEFAULT_RETRY = storage.retry.DEFAULT_RETRY.with_deadline(30.0)


class GCSPromptAdapter:
    """Stores versioned YAML prompts in GCS (`{prefix}/{id}/{ver}.yaml`)."""

    def __init__(
        self,
        bucket_name: str,
        *,
        prefix: str = "prompts",
        project_id: str | None = None,
        storage_client: Any | None = None,
        retry: gcp_retry.Retry | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if not bucket_name or not bucket_name.strip():
            raise exceptions.ConfigurationError("bucket_name is required.")
        self._bucket_name = settings.validate_gcp_identifier(
            bucket_name, "gcs_bucket"
        )
        self._prefix = prefix.strip("/")
        self._retry = retry if retry is not None else _DEFAULT_RETRY
        self._timeout = timeout_seconds
        proj, _ = settings.resolve_gcp_project_and_location(
            project_id, require_project=False
        )
        try:
            self._client = storage_client or storage.Client(project=proj)
            self._bucket = self._client.bucket(self._bucket_name)
        except Exception as exc:
            raise exceptions.PromptRegistryError(
                f"Failed initializing GCSPromptAdapter: {exc}"
            ) from exc

    def _prompt_prefix(self, prompt_id: str) -> str:
        cid = yaml_utils.validate_prompt_id(prompt_id)
        return f"{self._prefix}/{cid}/" if self._prefix else f"{cid}/"

    def _blob_name(self, prompt_id: str, version: str) -> str:
        cver = yaml_utils.validate_version_id(version)
        return f"{self._prompt_prefix(prompt_id)}{cver}.yaml"

    def _iter_blobs(self, prefix: str) -> Any:
        return self._client.list_blobs(
            self._bucket_name,
            prefix=prefix,
            retry=self._retry,
            timeout=self._timeout,
        )

    def get_prompt(
        self, prompt_id: str, version: str = "latest"
    ) -> prompt_ports.PromptTemplate:
        """Fetches a PromptTemplate from GCS by ID and SemVer or 'latest'."""
        cid = yaml_utils.validate_prompt_id(prompt_id)
        ver = (
            self.list_versions(cid)[-1]
            if version == "latest"
            else yaml_utils.validate_version_id(version)
        )
        blob = self._bucket.blob(self._blob_name(cid, ver))
        try:
            parsed = yaml.safe_load(
                blob.download_as_text(retry=self._retry, timeout=self._timeout)
            )
            if not isinstance(parsed, dict):
                raise exceptions.PromptRegistryError(
                    f"Malformed YAML payload for {cid!r}@{ver!r}."
                )
            parsed.setdefault("prompt_id", cid)
            parsed.setdefault("version", ver)
            return prompt_ports.PromptTemplate.model_validate(parsed)
        except gcp_exceptions.NotFound as exc:
            raise exceptions.PromptNotFoundError(
                f"GCS prompt {cid!r}@{ver!r} not found."
            ) from exc
        except (
            yaml.YAMLError,
            pydantic.ValidationError,
            gcp_exceptions.GoogleAPICallError,
        ) as exc:
            raise exceptions.PromptRegistryError(
                f"Failed reading GCS prompt {cid!r}@{ver!r}: {exc}"
            ) from exc

    def register_prompt(
        self,
        prompt: prompt_ports.PromptTemplate,
        *,
        if_generation_match: int | None = None,
    ) -> prompt_ports.PromptTemplate:
        """Uploads a versioned YAML prompt manifest to GCS."""
        cid = yaml_utils.validate_prompt_id(prompt.prompt_id)
        cver = yaml_utils.validate_version_id(prompt.version)
        norm = prompt.model_copy(update={"prompt_id": cid, "version": cver})
        blob = self._bucket.blob(self._blob_name(cid, cver))
        payload = yaml.safe_dump(norm.model_dump(mode="json"), sort_keys=False)
        extra = (
            {"if_generation_match": if_generation_match}
            if if_generation_match is not None
            else {}
        )
        try:
            blob.upload_from_string(
                payload,
                content_type="application/x-yaml",
                retry=self._retry,
                timeout=self._timeout,
                **extra,
            )
            return norm
        except Exception as exc:
            raise exceptions.PromptRegistryError(
                f"Failed uploading GCS prompt {cid!r}@{cver!r}: {exc}"
            ) from exc

    def list_prompts(self) -> list[str]:
        """Returns sorted prompt IDs stored under the configured GCS prefix."""
        root = f"{self._prefix}/" if self._prefix else ""
        try:
            ids: set[str] = set()
            for blob in self._iter_blobs(root):
                parts = str(blob.name)[len(root) :].split("/")
                if len(parts) == 2 and parts[1].endswith((".yaml", ".yml")):
                    try:
                        ids.add(yaml_utils.validate_prompt_id(parts[0]))
                    except ValueError:
                        continue
            return sorted(ids)
        except Exception as exc:
            raise exceptions.PromptRegistryError(
                f"Failed listing GCS prompts in {self._bucket_name!r}: {exc}"
            ) from exc

    def list_versions(self, prompt_id: str) -> list[str]:
        """Returns SemVer-sorted versions stored in GCS for prompt_id."""
        prefix = self._prompt_prefix(prompt_id)
        try:
            versions = [
                yaml_utils.validate_version_id(
                    str(b.name)[len(prefix) :].rsplit(".", 1)[0]
                )
                for b in self._iter_blobs(prefix)
                if "/" not in str(b.name)[len(prefix) :]
                and str(b.name).endswith((".yaml", ".yml"))
            ]
        except Exception as exc:
            raise exceptions.PromptRegistryError(
                f"Failed listing GCS versions for {prompt_id!r}: {exc}"
            ) from exc
        if not versions:
            raise exceptions.PromptNotFoundError(
                f"Prompt {prompt_id!r} not found in {self._bucket_name!r}."
            )
        return yaml_utils.sort_versions(versions)

    def delete_prompt(
        self,
        prompt_id: str,
        version: str | None = None,
        *,
        if_generation_match: int | None = None,
    ) -> bool:
        """Deletes all versions or a specific version of prompt_id from GCS."""
        cid = yaml_utils.validate_prompt_id(prompt_id)
        extra = (
            {"if_generation_match": if_generation_match}
            if if_generation_match is not None
            else {}
        )
        try:
            if version is not None:
                blob = self._bucket.blob(self._blob_name(cid, version))
                blob.delete(retry=self._retry, timeout=self._timeout, **extra)
                return True
            blobs = list(self._iter_blobs(self._prompt_prefix(cid)))
            for blob in blobs:
                blob.delete(retry=self._retry, timeout=self._timeout, **extra)
            return bool(blobs)
        except gcp_exceptions.NotFound:
            return False
        except Exception as exc:
            raise exceptions.PromptRegistryError(
                f"Failed deleting GCS prompt {cid!r}: {exc}"
            ) from exc
