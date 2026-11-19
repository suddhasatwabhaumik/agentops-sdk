# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""LSP contract tests for PromptBackendPort adapters."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from google.api_core import exceptions as gcp_exceptions
import pytest

from agentops_sdk.domain import exceptions
from agentops_sdk.ports import prompts as prompt_ports
from agentops_sdk.prompts import GCSPromptAdapter
from agentops_sdk.prompts import LocalYAMLPromptAdapter
from agentops_sdk.prompts import PromptTemplate
from agentops_sdk.prompts import VertexAIPromptAdapter
from agentops_sdk.prompts.in_memory import InMemoryPromptAdapter


class _FakeBlob:
    def __init__(self, store: dict[str, str], name: str) -> None:
        self._store = store
        self.name = name

    def upload_from_string(self, data: str, **_kw: Any) -> None:
        self._store[self.name] = data

    def download_as_text(self, **_kw: Any) -> str:
        if self.name not in self._store:
            raise gcp_exceptions.NotFound(self.name)
        return self._store[self.name]

    def delete(self, **_kw: Any) -> None:
        if self.name not in self._store:
            raise gcp_exceptions.NotFound(self.name)
        del self._store[self.name]


class _FakeGCSClient:
    def __init__(self) -> None:
        self._blobs: dict[str, str] = {}

    def bucket(self, _name: str) -> SimpleNamespace:
        return SimpleNamespace(blob=lambda key: _FakeBlob(self._blobs, key))

    def list_blobs(
        self, _bucket: str, prefix: str = "", **_kw: Any
    ) -> list[_FakeBlob]:
        return [
            _FakeBlob(self._blobs, k)
            for k in sorted(self._blobs)
            if k.startswith(prefix)
        ]


class _FakeVertexPromptsAPI:
    def __init__(self) -> None:
        self._datasets: dict[str, dict[str, Any]] = {}

    def list(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(prompt_id=k) for k in self._datasets]

    def get(self, *, prompt_id: str) -> SimpleNamespace:
        if prompt_id not in self._datasets:
            raise gcp_exceptions.NotFound(prompt_id)
        ds = self._datasets[prompt_id]
        latest = sorted(ds["versions"])[-1]
        return ds["versions"][latest]

    def create_version(self, *, prompt: Any, config: Any) -> SimpleNamespace:
        pid = config.prompt_display_name
        ver = config.version_display_name
        rec = SimpleNamespace(
            prompt_id=pid,
            version_id=ver,
            prompt_data=prompt.prompt_data,
            dataset=SimpleNamespace(display_name=pid, description=""),
        )
        ds = self._datasets.setdefault(pid, {"versions": {}})
        ds["versions"][ver] = rec
        return rec

    def update(
        self, *, prompt_id: str, prompt: Any, config: Any
    ) -> SimpleNamespace:
        if prompt_id not in self._datasets:
            raise gcp_exceptions.NotFound(prompt_id)
        return self.create_version(prompt=prompt, config=config)

    def get_version(
        self, *, prompt_id: str, version_id: str
    ) -> SimpleNamespace:
        ds = self._datasets.get(prompt_id)
        if not ds or version_id not in ds["versions"]:
            raise gcp_exceptions.NotFound(f"{prompt_id}@{version_id}")
        return ds["versions"][version_id]

    def list_versions(self, *, prompt_id: str) -> list[SimpleNamespace]:
        ds = self._datasets.get(prompt_id)
        if not ds:
            raise gcp_exceptions.NotFound(prompt_id)
        return [SimpleNamespace(version_id=v) for v in ds["versions"]]

    def delete(self, *, prompt_id: str) -> None:
        if prompt_id not in self._datasets:
            raise gcp_exceptions.NotFound(prompt_id)
        del self._datasets[prompt_id]

    def delete_version(self, *, prompt_id: str, version_id: str) -> None:
        ds = self._datasets.get(prompt_id)
        if not ds or version_id not in ds["versions"]:
            raise gcp_exceptions.NotFound(version_id)
        del ds["versions"][version_id]
        if not ds["versions"]:
            del self._datasets[prompt_id]


@pytest.fixture(params=["in_memory", "local_yaml", "gcs", "vertex_ai"])
def prompt_adapter(
    request: pytest.FixtureRequest, tmp_path: Path
) -> prompt_ports.PromptBackendPort:
    """Provides PromptBackendPort implementations under identical contract."""
    if request.param == "in_memory":
        return InMemoryPromptAdapter()
    if request.param == "local_yaml":
        return LocalYAMLPromptAdapter(local_dir=str(tmp_path / "prompts"))
    if request.param == "gcs":
        return GCSPromptAdapter(
            "unit-prompt-bucket", storage_client=_FakeGCSClient()
        )
    client = SimpleNamespace(prompts=_FakeVertexPromptsAPI())
    return VertexAIPromptAdapter("test-proj", "europe-west4", client=client)


def test_prompt_crud_and_semver_resolution(
    prompt_adapter: prompt_ports.PromptBackendPort,
) -> None:
    assert isinstance(prompt_adapter, prompt_ports.PromptBackendPort)
    assert prompt_adapter.list_prompts() == []
    for ver in ("1.10.0", "1.2.0", "1.9.0"):
        prompt_adapter.register_prompt(
            PromptTemplate(
                prompt_id="sales-summary",
                version=ver,
                template=f"Summarize v{ver}: {{topic}}",
                system_instruction="Be accurate.",
                generation_config={"temperature": 0.2},
                variables=("topic",),
            )
        )
    prompt_adapter.register_prompt(
        PromptTemplate(prompt_id="alpha-guide", version="1.0.0", template="Hi")
    )
    assert prompt_adapter.list_prompts() == ["alpha-guide", "sales-summary"]
    assert prompt_adapter.list_versions("sales-summary") == [
        "1.2.0",
        "1.9.0",
        "1.10.0",
    ]
    latest = prompt_adapter.get_prompt("sales-summary")
    assert latest.version == "1.10.0"
    assert latest.system_instruction == "Be accurate."
    assert latest.generation_config.get("temperature") == 0.2
    assert latest.variables == ("topic",)
    assert prompt_adapter.get_prompt("sales-summary", "1.2.0").version == (
        "1.2.0"
    )


def test_prompt_delete_and_not_found(
    prompt_adapter: prompt_ports.PromptBackendPort,
) -> None:
    prompt_adapter.register_prompt(
        PromptTemplate(prompt_id="temp", version="1.0.0", template="v1")
    )
    prompt_adapter.register_prompt(
        PromptTemplate(prompt_id="temp", version="2.0.0", template="v2")
    )
    assert prompt_adapter.delete_prompt("temp", version="9.9.9") is False
    assert prompt_adapter.delete_prompt("temp", version="1.0.0") is True
    assert prompt_adapter.list_versions("temp") == ["2.0.0"]
    with pytest.raises(exceptions.PromptNotFoundError):
        prompt_adapter.get_prompt("temp", "9.9.9")
    assert prompt_adapter.delete_prompt("temp") is True
    assert prompt_adapter.delete_prompt("temp") is False
    assert prompt_adapter.list_prompts() == []
    with pytest.raises(exceptions.PromptNotFoundError):
        prompt_adapter.get_prompt("temp")
    with pytest.raises(exceptions.PromptNotFoundError):
        prompt_adapter.list_versions("missing")


def test_prompt_path_traversal_rejected(
    prompt_adapter: prompt_ports.PromptBackendPort,
) -> None:
    for bad in ("../escape", "nested/id", "win\\id", ""):
        with pytest.raises(ValueError):
            prompt_adapter.get_prompt(bad)
        with pytest.raises(ValueError):
            prompt_adapter.delete_prompt(bad)


def test_local_yaml_rejects_symlink(tmp_path: Path) -> None:
    pdir = tmp_path / "prompts"
    pdir.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("prompt_id: evil\ntemplate: leak\n", encoding="utf-8")
    os.symlink(outside, pdir / "evil.yaml")
    adapter = LocalYAMLPromptAdapter(local_dir=str(pdir))
    with pytest.raises(exceptions.PromptRegistryError, match="Symlink denied"):
        adapter.get_prompt("evil")
