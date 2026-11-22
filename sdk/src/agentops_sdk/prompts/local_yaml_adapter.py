# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Local YAML file adapter and identifier/SemVer utilities for prompts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import contextlib
import os
import re
import threading
from typing import Any
import uuid

from packaging import version as pkg_version
import pydantic
import yaml

from agentops_sdk.domain import exceptions
from agentops_sdk.ports import prompts as prompt_ports

_PROMPT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_VERSION_ID_RE = re.compile(r"^[A-Za-z0-9.+_-]{1,128}$")


def _validate_identifier(
    value: str, label: str, pattern: re.Pattern[str]
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    cleaned = value.strip()
    if ".." in cleaned or "/" in cleaned or "\\" in cleaned:
        raise ValueError(f"Invalid {label} {value!r}: path traversal.")
    if cleaned in {".", ".."} or not pattern.match(cleaned):
        raise ValueError(f"Invalid {label} format: {value!r}.")
    return cleaned


def validate_prompt_id(prompt_id: str) -> str:
    """Validates prompt_id against path traversal and character bounds."""
    return _validate_identifier(prompt_id, "prompt_id", _PROMPT_ID_RE)


def validate_version_id(version: str) -> str:
    """Validates a prompt version identifier for safe storage keys."""
    return _validate_identifier(version, "version", _VERSION_ID_RE)


class _VersionSortKey:
    """Comparable key sorting non-SemVer tags before SemVer releases."""

    __slots__ = ("_is_semver", "_parsed", "_raw")

    def __init__(self, raw: str) -> None:
        self._raw = raw
        try:
            self._parsed: pkg_version.Version | None = pkg_version.Version(raw)
            self._is_semver = True
        except pkg_version.InvalidVersion:
            self._parsed = None
            self._is_semver = False

    def __lt__(self, other: _VersionSortKey) -> bool:
        if self._is_semver != other._is_semver:
            return not self._is_semver
        if self._parsed and other._parsed and self._parsed != other._parsed:
            return self._parsed < other._parsed
        return self._raw < other._raw


def sort_versions(versions: Iterable[str]) -> list[str]:
    """Returns version strings sorted in ascending SemVer order."""
    return sorted(versions, key=_VersionSortKey)


def _coerce_template(
    entry: Mapping[str, Any], default_id: str, default_ver: str = "1.0.0"
) -> prompt_ports.PromptTemplate:
    raw_vars = entry.get("variables") or ()
    is_seq = isinstance(raw_vars, Iterable) and not isinstance(
        raw_vars, (str, bytes)
    )
    return prompt_ports.PromptTemplate(
        prompt_id=validate_prompt_id(str(entry.get("prompt_id", default_id))),
        version=validate_version_id(str(entry.get("version", default_ver))),
        template=str(entry["template"]),
        description=str(entry.get("description", "")),
        model_name=str(entry.get("model_name", "gemini-2.5-flash")),
        system_instruction=entry.get("system_instruction"),
        variables=tuple(str(v) for v in raw_vars) if is_seq else (),
        generation_config=dict(entry.get("generation_config") or {}),
        metadata=dict(entry.get("metadata") or {}),
    )


def _parse_yaml_document(
    raw_data: Any, default_id: str
) -> list[prompt_ports.PromptTemplate]:
    if isinstance(raw_data, list):
        return [
            _coerce_template(i, default_id)
            for i in raw_data
            if isinstance(i, Mapping) and "template" in i
        ]
    if not isinstance(raw_data, Mapping):
        return []
    doc_id = str(raw_data.get("prompt_id") or default_id)
    vmap = raw_data.get("versions")
    if isinstance(vmap, Mapping):
        return [
            _coerce_template(i, doc_id, str(k))
            for k, i in vmap.items()
            if isinstance(i, Mapping) and "template" in i
        ]
    return (
        [_coerce_template(raw_data, doc_id)] if "template" in raw_data else []
    )


class LocalYAMLPromptAdapter:
    """Thread-safe local YAML prompt adapter with symlink containment."""

    def __init__(self, local_dir: str = "prompts") -> None:
        """Initializes LocalYAMLPromptAdapter rooted at local_dir."""
        if not local_dir or not local_dir.strip():
            raise ValueError("local_dir must be a non-empty path.")
        self._local_dir = local_dir
        self._lock = threading.RLock()

    def _resolve_contained_path(self, filename: str) -> str:
        base_dir = os.path.realpath(self._local_dir)
        candidate = os.path.join(base_dir, filename)
        if os.path.islink(candidate):
            raise exceptions.PromptRegistryError(
                f"Symlink denied: {filename!r}"
            )
        resolved = os.path.realpath(candidate)
        prefix = (
            base_dir if base_dir.endswith(os.sep) else f"{base_dir}{os.sep}"
        )
        if (
            os.path.commonpath([base_dir, resolved]) != base_dir
            or resolved == base_dir
            or not resolved.startswith(prefix)
        ):
            raise exceptions.PromptRegistryError(f"Path escape: {filename!r}")
        return resolved

    def _read_yaml_file(
        self, filename: str, default_id: str
    ) -> list[prompt_ports.PromptTemplate]:
        resolved = self._resolve_contained_path(filename)
        if not os.path.isfile(resolved):
            return []
        try:
            with open(resolved, encoding="utf-8") as handle:
                return _parse_yaml_document(yaml.safe_load(handle), default_id)
        except (
            OSError,
            yaml.YAMLError,
            pydantic.ValidationError,
            ValueError,
        ) as e:
            raise exceptions.PromptRegistryError(
                f"Failed reading {filename!r}: {e}"
            ) from e

    def _collect_all(self) -> dict[str, dict[str, prompt_ports.PromptTemplate]]:
        base_dir = os.path.realpath(self._local_dir)
        if not os.path.isdir(base_dir):
            return {}
        catalog: dict[str, dict[str, prompt_ports.PromptTemplate]] = {}
        for entry in sorted(os.listdir(base_dir)):
            stem, dot, ext = entry.rpartition(".")
            if dot and ext in {"yaml", "yml"} and _PROMPT_ID_RE.match(stem):
                for tmpl in self._read_yaml_file(entry, stem):
                    catalog.setdefault(tmpl.prompt_id, {})[tmpl.version] = tmpl
        return catalog

    def _load_versions_map(
        self, prompt_id: str
    ) -> dict[str, prompt_ports.PromptTemplate]:
        clean_id = validate_prompt_id(prompt_id)
        direct: dict[str, prompt_ports.PromptTemplate] = {}
        for ext in (".yaml", ".yml"):
            for tmpl in self._read_yaml_file(f"{clean_id}{ext}", clean_id):
                if tmpl.prompt_id == clean_id:
                    direct[tmpl.version] = tmpl
        return direct if direct else self._collect_all().get(clean_id, {})

    def _write_versions_file(
        self,
        clean_id: str,
        versions_map: Mapping[str, prompt_ports.PromptTemplate],
    ) -> None:
        target = self._resolve_contained_path(f"{clean_id}.yaml")
        vers = {
            v: versions_map[v].model_dump(mode="json")
            for v in sort_versions(versions_map)
        }
        payload = {"prompt_id": clean_id, "versions": vers}
        parent, name = (
            os.path.dirname(target),
            f".{clean_id}.{uuid.uuid4().hex}",
        )
        tmp_path = os.path.join(parent, f"{name}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        try:
            fd = os.open(tmp_path, flags, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump(payload, handle, sort_keys=False)
                handle.flush()
                os.fsync(handle.fileno())
            if os.path.islink(target):
                raise exceptions.PromptRegistryError(
                    f"Symlink denied: {clean_id!r}"
                )
            os.replace(tmp_path, target)
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            if isinstance(exc, exceptions.PromptRegistryError):
                raise
            raise exceptions.PromptRegistryError(
                f"Failed writing {clean_id!r}: {exc}"
            ) from exc

    def get_prompt(
        self, prompt_id: str, version: str = "latest"
    ) -> prompt_ports.PromptTemplate:
        """Fetches a PromptTemplate by ID and SemVer or 'latest'."""
        with self._lock:
            vmap = self._load_versions_map(prompt_id)
            if not vmap:
                raise exceptions.PromptNotFoundError(
                    f"Prompt {prompt_id!r} not found."
                )
            ver = (
                sort_versions(vmap)[-1]
                if version == "latest"
                else validate_version_id(version)
            )
            if ver not in vmap:
                raise exceptions.PromptNotFoundError(
                    f"Version {ver!r} not found for {prompt_id!r}."
                )
            return vmap[ver]

    def register_prompt(
        self, prompt: prompt_ports.PromptTemplate
    ) -> prompt_ports.PromptTemplate:
        """Persists a prompt version while preserving all prior versions."""
        cid = validate_prompt_id(prompt.prompt_id)
        cver = validate_version_id(prompt.version)
        norm = prompt.model_copy(update={"prompt_id": cid, "version": cver})
        with self._lock:
            os.makedirs(self._local_dir, mode=0o700, exist_ok=True)
            vmap = dict(self._load_versions_map(cid))
            vmap[cver] = norm
            self._write_versions_file(cid, vmap)
            return norm

    def list_prompts(self) -> list[str]:
        """Returns sorted prompt IDs available in local_dir."""
        with self._lock:
            return sorted(self._collect_all())

    def list_versions(self, prompt_id: str) -> list[str]:
        """Returns SemVer-sorted versions registered for prompt_id."""
        with self._lock:
            vmap = self._load_versions_map(prompt_id)
            if not vmap:
                raise exceptions.PromptNotFoundError(
                    f"Prompt {prompt_id!r} not found."
                )
            return sort_versions(vmap)

    def delete_prompt(self, prompt_id: str, version: str | None = None) -> bool:
        """Deletes all versions or a single version for prompt_id."""
        cid = validate_prompt_id(prompt_id)
        with self._lock:
            vmap = dict(self._load_versions_map(cid))
            if not vmap:
                return False
            target = self._resolve_contained_path(f"{cid}.yaml")
            if version is None:
                if os.path.isfile(target):
                    os.remove(target)
                return True
            cver = validate_version_id(version)
            if cver not in vmap:
                return False
            del vmap[cver]
            if not vmap and os.path.isfile(target):
                os.remove(target)
            elif vmap:
                self._write_versions_file(cid, vmap)
            return True
