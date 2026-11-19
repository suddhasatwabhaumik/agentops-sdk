# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Thread-safe in-memory PromptBackendPort adapter."""

from __future__ import annotations

import threading

from agentops_sdk.domain import exceptions
from agentops_sdk.ports import prompts as prompt_ports
from agentops_sdk.prompts import local_yaml_adapter as yaml_utils


class InMemoryPromptAdapter:
    """Thread-safe in-memory PromptBackendPort implementation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._store: dict[str, dict[str, prompt_ports.PromptTemplate]] = {}

    def get_prompt(
        self, prompt_id: str, version: str = "latest"
    ) -> prompt_ports.PromptTemplate:
        """Fetches a PromptTemplate by ID and SemVer or 'latest'."""
        cid = yaml_utils.validate_prompt_id(prompt_id)
        with self._lock:
            vers = self._store.get(cid)
            if not vers:
                raise exceptions.PromptNotFoundError(f"Prompt {cid!r} missing.")
            target = (
                yaml_utils.sort_versions(vers)[-1]
                if version == "latest"
                else yaml_utils.validate_version_id(version)
            )
            if target not in vers:
                raise exceptions.PromptNotFoundError(
                    f"Version {target!r} missing for {cid!r}."
                )
            return vers[target]

    def register_prompt(
        self, prompt: prompt_ports.PromptTemplate
    ) -> prompt_ports.PromptTemplate:
        """Stores a new prompt version in memory."""
        cid = yaml_utils.validate_prompt_id(prompt.prompt_id)
        cver = yaml_utils.validate_version_id(prompt.version)
        norm = prompt.model_copy(update={"prompt_id": cid, "version": cver})
        with self._lock:
            self._store.setdefault(cid, {})[cver] = norm
            return norm

    def list_prompts(self) -> list[str]:
        """Returns sorted prompt IDs in memory."""
        with self._lock:
            return sorted(self._store)

    def list_versions(self, prompt_id: str) -> list[str]:
        """Returns SemVer-sorted versions registered for prompt_id."""
        cid = yaml_utils.validate_prompt_id(prompt_id)
        with self._lock:
            vers = self._store.get(cid)
            if not vers:
                raise exceptions.PromptNotFoundError(f"Prompt {cid!r} missing.")
            return yaml_utils.sort_versions(vers)

    def delete_prompt(self, prompt_id: str, version: str | None = None) -> bool:
        """Deletes a prompt or specific version from memory."""
        cid = yaml_utils.validate_prompt_id(prompt_id)
        with self._lock:
            vers = self._store.get(cid)
            if not vers:
                return False
            if version is None:
                del self._store[cid]
                return True
            removed = vers.pop(yaml_utils.validate_version_id(version), None)
            if not vers:
                del self._store[cid]
            return removed is not None
