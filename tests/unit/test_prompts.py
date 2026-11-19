# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for agentops_sdk.prompts registry and backend adapters."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from google.genai import errors as genai_errors
import pytest

from agentops_sdk.domain import exceptions
from agentops_sdk.prompts import GCSPromptAdapter
from agentops_sdk.prompts import InMemoryPromptAdapter
from agentops_sdk.prompts import LocalYAMLPromptAdapter
from agentops_sdk.prompts import PromptRegistry
from agentops_sdk.prompts import PromptTemplate
from agentops_sdk.prompts import VertexAIPromptAdapter
from agentops_sdk.prompts import _vertex_codec
from agentops_sdk.prompts import sort_versions
from agentops_sdk.prompts import validate_prompt_id
from agentops_sdk.prompts import validate_version_id


def test_identifiers_and_semver_sorting() -> None:
    assert validate_prompt_id("sql_advisor-v1.0") == "sql_advisor-v1.0"
    assert validate_version_id("1.2.3+build.1") == "1.2.3+build.1"
    for bad in ("", " ", "../x", "a/b", "a\\b", "."):
        with pytest.raises(ValueError):
            validate_prompt_id(bad)
        with pytest.raises(ValueError):
            validate_version_id(bad)
    assert sort_versions(["1.10.0", "latest", "1.2.0", "1.9.0"]) == [
        "latest",
        "1.2.0",
        "1.9.0",
        "1.10.0",
    ]


def test_local_yaml_reads_list_single_and_versions_map(
    tmp_path: Path,
) -> None:
    pdir = tmp_path / "prompts"
    pdir.mkdir()
    (pdir / "list_prompt.yaml").write_text(
        "- version: 1.0.0\n  template: Hi {name}\n"
        "- version: 2.0.0\n  template: Hello {name}\n",
        encoding="utf-8",
    )
    (pdir / "single.yml").write_text(
        "prompt_id: single\ntemplate: One-shot\n", encoding="utf-8"
    )
    (pdir / "mapped.yaml").write_text(
        "prompt_id: mapped\nversions:\n"
        "  '1.0.0':\n    template: v1\n"
        "  '2.0.0':\n    template: v2\n",
        encoding="utf-8",
    )
    adapter = LocalYAMLPromptAdapter(local_dir=str(pdir))
    assert adapter.list_prompts() == ["list_prompt", "mapped", "single"]
    assert adapter.list_versions("mapped") == ["1.0.0", "2.0.0"]
    assert adapter.get_prompt("mapped").template == "v2"
    assert adapter.get_prompt("list_prompt").version == "2.0.0"
    assert adapter.get_prompt("single").template == "One-shot"


def test_prompt_registry_rendering_and_ssti_protection() -> None:
    reg = PromptRegistry(backend="in_memory")
    reg.register_prompt(
        "greet",
        template="Hello {user} {% raw %}{ignored}{% endraw %}",
        version="1.0.0",
    )
    rendered = reg.render_prompt("greet", {"user": "Alice"})
    assert rendered == "Hello Alice {ignored}"
    assert reg.format_prompt("greet", {"user": "Bob"}) == (
        "Hello Bob {ignored}"
    )
    tmpl = PromptTemplate(prompt_id="inline", template="Hi {{ name }}")
    assert reg.format_prompt(tmpl, {"name": "Sam"}) == "Hi Sam"

    with pytest.raises(exceptions.PromptRegistryError):
        reg.format_prompt("{{ ''.__class__.__mro__ }}")
    with pytest.raises(exceptions.PromptRegistryError, match="max size"):
        reg.format_prompt("x" * 1_000_001)


def test_prompt_registry_fallback_and_config_errors(tmp_path: Path) -> None:
    with pytest.raises(exceptions.ConfigurationError):
        PromptRegistry(backend="unknown-backend")
    with pytest.raises(exceptions.ConfigurationError):
        PromptRegistry(backend="gcs")

    pdir = tmp_path / "local_prompts"
    local = LocalYAMLPromptAdapter(local_dir=str(pdir))
    local.register_prompt(
        PromptTemplate(prompt_id="p1", version="1.0.0", template="Local {x}")
    )

    class _FailingBackend(InMemoryPromptAdapter):
        def get_prompt(
            self, prompt_id: str, version: str = "latest"
        ) -> PromptTemplate:
            raise exceptions.PromptRegistryError("GCS unavailable")

    reg = PromptRegistry(
        adapter=_FailingBackend(),
        fallback_adapter=local,
        fallback_to_local=True,
    )
    assert reg.render_prompt("p1", {"x": "ok"}) == "Local ok"


def test_vertex_codec_vars_list_and_display_name() -> None:
    vp_vars = _vertex_codec.build_vertex_prompt(
        PromptTemplate(
            prompt_id="p", template="Hi {a} {b}", variables=("a", "b")
        )
    )
    assert vp_vars.prompt_data is not None
    assert len(vp_vars.prompt_data.variables or []) == 1
    assert set((vp_vars.prompt_data.variables or [{}])[0]) == {"a", "b"}
    plain_vars = _vertex_codec.build_vertex_prompt(
        PromptTemplate(prompt_id="p", template="Plain")
    ).prompt_data.variables
    assert plain_vars is None
    raw = SimpleNamespace(
        dataset=SimpleNamespace(display_name="sales", description="desc"),
        dataset_version=SimpleNamespace(display_name="2.5.0", metadata=None),
        prompt_data=vp_vars.prompt_data,
        prompt_id="ds-1",
        version_id="99",
    )
    parsed = _vertex_codec.to_domain_template(raw, "sales", "1.0.0")
    assert parsed.version == "2.5.0"


def test_vertex_adapter_404_evicts_id_cache() -> None:
    err_404 = genai_errors.ClientError(404, {"error": {"message": "missing"}})
    vtx_api = SimpleNamespace(
        list=lambda: [SimpleNamespace(prompt_id="ds-1")],
        _list_versions_pager=lambda **_kw: [
            SimpleNamespace(name="v/99", display_name="2.5.0")
        ],
        get_version=lambda **_kw: (_ for _ in ()).throw(err_404),
    )
    vtx = VertexAIPromptAdapter(
        "test-proj", "europe-west4", client=SimpleNamespace(prompts=vtx_api)
    )
    vtx._id_cache["sales"] = "ds-1"
    with pytest.raises(exceptions.PromptNotFoundError):
        vtx.get_prompt("sales", "2.5.0")
    assert "sales" not in vtx._id_cache


def test_gcs_and_vertex_adapter_error_handling() -> None:
    with pytest.raises(exceptions.ConfigurationError):
        GCSPromptAdapter("")
    bad_blob = SimpleNamespace(download_as_text=lambda **_kw: "not-a-dict")
    bad_gcs = SimpleNamespace(
        bucket=lambda _n: SimpleNamespace(blob=lambda _k: bad_blob),
        list_blobs=lambda *_a, **_kw: [
            SimpleNamespace(name="prompts/p/1.yaml")
        ],
    )
    gcs = GCSPromptAdapter("bucket-1", storage_client=bad_gcs)
    with pytest.raises(exceptions.PromptRegistryError, match="Malformed"):
        gcs.get_prompt("p", version="1.0.0")

    bad_vtx = SimpleNamespace(
        prompts=SimpleNamespace(
            list=lambda: (_ for _ in ()).throw(RuntimeError("Vertex 500"))
        )
    )
    vtx = VertexAIPromptAdapter("test-proj", "europe-west4", client=bad_vtx)
    with pytest.raises(exceptions.PromptRegistryError, match="Vertex list"):
        vtx.list_prompts()
    with pytest.raises(exceptions.PromptRegistryError):
        vtx.register_prompt(
            PromptTemplate(prompt_id="p", version="1.0.0", template="hi")
        )
