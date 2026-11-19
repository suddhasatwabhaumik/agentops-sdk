# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Stateless Vertex AI Prompt <-> Domain PromptTemplate conversion helpers."""

from __future__ import annotations

from collections.abc import Mapping
import contextlib
from typing import Any

from google.api_core import exceptions as gcp_exceptions
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from vertexai._genai import _prompt_management_utils as pmu
from vertexai._genai import types as vertex_types

from agentops_sdk.ports import prompts as prompt_ports


def is_not_found(exc: BaseException) -> bool:
    """Returns True for GCP NotFound or HTTP 404 GenAI APIError."""
    return isinstance(exc, gcp_exceptions.NotFound) or (
        isinstance(exc, genai_errors.APIError)
        and getattr(exc, "code", None) == 404
    )


def extract_text(content: Any) -> str:
    """Extracts concatenated Part.text strings from Vertex Content."""
    if content is None or isinstance(content, str):
        return content or ""
    texts: list[str] = []
    for item in content if isinstance(content, list) else [content]:
        for part in getattr(item, "parts", None) or ():
            txt = getattr(part, "text", None)
            if isinstance(txt, str) and txt:
                texts.append(txt)
    return "\n".join(texts)


def extract_gen_cfg(raw_cfg: Any) -> dict[str, Any]:
    """Converts a Vertex GenerationConfig or mapping into a plain dict."""
    if isinstance(raw_cfg, Mapping):
        return dict(raw_cfg)
    dump_fn = getattr(raw_cfg, "model_dump", None)
    dumped = dump_fn(exclude_none=True) if callable(dump_fn) else None
    return dict(dumped) if isinstance(dumped, Mapping) else {}


def build_vertex_prompt(
    prompt: prompt_ports.PromptTemplate,
) -> vertex_types.Prompt:
    """Builds a Vertex AI Prompt resource with single non-empty var dict."""
    p_fn = genai_types.Part
    c_fn = genai_types.Content
    sys_txt = prompt.system_instruction
    sys_inst = c_fn(parts=[p_fn(text=sys_txt)]) if sys_txt else None
    gen_cfg = (
        genai_types.GenerationConfig(**prompt.generation_config)
        if prompt.generation_config
        else None
    )
    vars_list = (
        [{k: p_fn(text=f"{{{k}}}") for k in prompt.variables}]
        if prompt.variables
        else None
    )
    pdata = vertex_types.PromptData(
        model=(prompt.model_name or "gemini-2.5-flash").strip(),
        contents=[c_fn(role="user", parts=[p_fn(text=prompt.template)])],
        system_instruction=sys_inst,
        generation_config=gen_cfg,
        variables=vars_list,
    )
    return vertex_types.Prompt(prompt_data=pdata)


def extract_var_names(raw_vars: Any) -> tuple[str, ...]:
    """Collects ordered unique variable names from Vertex execution maps."""
    if not isinstance(raw_vars, list):
        return ()
    seen: dict[str, None] = {}
    for mapping in raw_vars:
        if isinstance(mapping, Mapping):
            for key in mapping:
                seen[str(key)] = None
    return tuple(seen)


def _reconstruct_prompt(vp: Any) -> Any:
    ds_ver = getattr(vp, "dataset_version", None)
    if ds_ver is not None and isinstance(
        getattr(ds_ver, "metadata", None),
        vertex_types.SchemaTextPromptDatasetMetadata,
    ):
        with contextlib.suppress(ValueError):
            return pmu._create_prompt_from_dataset_metadata(ds_ver)
    return vp


def to_domain_template(
    vp: Any,
    logical_id: str,
    fallback_ver: str,
    fallback_desc: str = "",
    extra_meta: Mapping[str, Any] | None = None,
) -> prompt_ports.PromptTemplate:
    """Converts a Vertex AI Prompt into a domain PromptTemplate."""
    hist = _reconstruct_prompt(vp)
    pdata = getattr(hist, "prompt_data", None) or getattr(
        vp, "prompt_data", None
    )
    raw_model = str(getattr(pdata, "model", None) or "gemini-2.5-flash")
    meta = dict(extra_meta or {})
    if pid := getattr(vp, "prompt_id", None):
        meta["vertex_prompt_id"] = str(pid)
    if vid := getattr(vp, "version_id", None):
        meta["vertex_version_id"] = str(vid)
    ds_ver = getattr(vp, "dataset_version", None)
    ver_disp = getattr(ds_ver, "display_name", None)
    ds_desc = getattr(getattr(vp, "dataset", None), "description", None)
    sys_inst = getattr(pdata, "system_instruction", None)
    gen_cfg = getattr(pdata, "generation_config", None)
    return prompt_ports.PromptTemplate(
        prompt_id=logical_id,
        version=str(ver_disp or vid or fallback_ver),
        template=extract_text(getattr(pdata, "contents", None)),
        description=str(ds_desc or fallback_desc),
        model_name=raw_model.split("/")[-1] or "gemini-2.5-flash",
        system_instruction=extract_text(sys_inst) or None,
        variables=extract_var_names(getattr(pdata, "variables", None)),
        generation_config=extract_gen_cfg(gen_cfg),
        metadata=meta,
    )
