# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""PromptRegistry facade and single-pass sandboxed Jinja2 rendering engine."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import logging
from typing import Any, TypeVar

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import templating
from agentops_sdk.ports import prompts as prompt_ports
from agentops_sdk.prompts import in_memory
from agentops_sdk.prompts import local_yaml_adapter as yaml_utils
from agentops_sdk.telemetry import provider

logger = logging.getLogger(__name__)

_T = TypeVar("_T")
_VALID_BACKENDS = frozenset({"local", "git", "vertex_ai", "gcs", "in_memory"})
InMemoryPromptAdapter = in_memory.InMemoryPromptAdapter


def _build_adapter(
    name: str,
    ldir: str,
    bkt: str | None,
    pfx: str,
    proj: str | None,
    loc: str | None,
) -> prompt_ports.PromptBackendPort:
    if name in {"local", "git"}:
        return yaml_utils.LocalYAMLPromptAdapter(local_dir=ldir)
    if name == "in_memory":
        return in_memory.InMemoryPromptAdapter()
    if name == "vertex_ai":
        from agentops_sdk.prompts import vertex_adapter

        return vertex_adapter.VertexAIPromptAdapter(proj, loc)
    if name == "gcs":
        if not bkt:
            raise exceptions.ConfigurationError("gcs_bucket is required.")
        from agentops_sdk.prompts import gcs_adapter

        return gcs_adapter.GCSPromptAdapter(bkt, prefix=pfx, project_id=proj)
    raise exceptions.ConfigurationError(f"Unsupported backend: {name!r}")


def _render_sandboxed(
    template_str: str, variables: Mapping[str, Any] | None
) -> str:
    tracker = provider.get_meter_tracker()
    err_attrs = {"status": "error", "engine": "jinja2_sandboxed"}
    try:
        out = templating.render_sandboxed_template(template_str, variables)
    except ValueError as exc:
        tracker.add_counter("agentops.prompt.renders.total", 1, err_attrs)
        raise exceptions.PromptRegistryError(str(exc)) from exc
    tracker.add_counter(
        "agentops.prompt.renders.total",
        1,
        {"status": "success", "engine": "jinja2_sandboxed"},
    )
    return out


class PromptRegistry:
    """Facade for versioned prompt management and sandboxed rendering."""

    def __init__(
        self,
        *,
        backend: str | None = None,
        adapter: prompt_ports.PromptBackendPort | None = None,
        fallback_adapter: prompt_ports.PromptBackendPort | None = None,
        local_dir: str | None = None,
        gcs_bucket: str | None = None,
        project_id: str | None = None,
        location: str | None = None,
        fallback_to_local: bool | None = None,
    ) -> None:
        """Initializes PromptRegistry with primary and optional fallback."""
        cfg = settings.get_prompt_settings()
        eff = (backend or cfg.backend).strip().lower()
        if adapter is None and eff not in _VALID_BACKENDS:
            raise exceptions.ConfigurationError(f"Unsupported backend: {eff!r}")
        ldir = local_dir if local_dir is not None else cfg.local_dir
        fb = (
            cfg.fallback_to_local
            if fallback_to_local is None
            else fallback_to_local
        )
        self.backend = eff if adapter is None else (backend or "custom")
        self._fb_label = "local"
        self._fallback_adapter = fallback_adapter or (
            yaml_utils.LocalYAMLPromptAdapter(local_dir=ldir)
            if fb and eff not in {"local", "git"}
            else None
        )
        bkt = cfg.gcs_bucket if gcs_bucket is None else gcs_bucket
        proj = cfg.project_id if project_id is None else project_id
        loc = cfg.location if location is None else location
        self._adapter = self._init_primary(
            adapter, eff, (ldir, bkt, cfg.gcs_prefix, proj, loc)
        )

    def _init_primary(
        self,
        adapter: prompt_ports.PromptBackendPort | None,
        eff: str,
        args: tuple[str, str | None, str, str | None, str | None],
    ) -> prompt_ports.PromptBackendPort:
        if adapter is not None:
            return adapter
        try:
            return _build_adapter(eff, *args)
        except exceptions.PromptRegistryError as exc:
            if self._fallback_adapter is None:
                raise
            logger.warning("Backend %s failed (%s); using fallback.", eff, exc)
            provider.get_meter_tracker().add_counter(
                "agentops.prompt.fallbacks.total",
                1,
                {"from_backend": eff, "to_backend": self._fb_label},
            )
            self.backend = self._fb_label
            return self._fallback_adapter

    def _record_load(self, backend: str, status: str) -> None:
        provider.get_meter_tracker().add_counter(
            "agentops.prompt.loads.total",
            1,
            {"backend": backend, "status": status},
        )

    def _run_read(
        self, op: Callable[[prompt_ports.PromptBackendPort], _T]
    ) -> _T:
        try:
            res = op(self._adapter)
            self._record_load(self.backend, "success")
            return res
        except ValueError:
            self._record_load(self.backend, "error")
            raise
        except Exception as exc:
            self._record_load(self.backend, "error")
            if (
                not self._fallback_adapter
                or self._fallback_adapter is self._adapter
            ):
                raise
            provider.get_meter_tracker().add_counter(
                "agentops.prompt.fallbacks.total",
                1,
                {"from_backend": self.backend, "to_backend": self._fb_label},
            )
            logger.warning(
                "Read on %s failed (%s); falling back.", self.backend, exc
            )
            res = op(self._fallback_adapter)
            self._record_load(f"{self._fb_label}_fallback", "success")
            return res

    def get_prompt(
        self, prompt_id: str, version: str = "latest"
    ) -> prompt_ports.PromptTemplate:
        """Fetches a PromptTemplate from the configured backend."""
        return self._run_read(
            lambda b: b.get_prompt(prompt_id, version=version)
        )

    def register_prompt(
        self,
        prompt: prompt_ports.PromptTemplate | str | None = None,
        *,
        prompt_id: str | None = None,
        template: str | None = None,
        version: str = "1.0.0",
        description: str = "",
        model_name: str = "gemini-2.5-flash",
        system_instruction: str | None = None,
        variables: Sequence[str] = (),
        generation_config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> prompt_ports.PromptTemplate:
        """Registers a PromptTemplate instance or inline prompt definition."""
        if isinstance(prompt, prompt_ports.PromptTemplate):
            return self._adapter.register_prompt(prompt)
        rid = prompt if isinstance(prompt, str) else prompt_id
        if rid is None or template is None:
            raise ValueError("Provide PromptTemplate or prompt_id + template.")
        return self._adapter.register_prompt(
            prompt_ports.PromptTemplate(
                prompt_id=yaml_utils.validate_prompt_id(rid),
                version=yaml_utils.validate_version_id(version),
                template=template,
                description=description,
                model_name=model_name,
                system_instruction=system_instruction,
                variables=tuple(variables),
                generation_config=dict(generation_config or {}),
                metadata=dict(metadata or {}),
            )
        )

    def list_prompts(self) -> list[str]:
        """Lists sorted prompt IDs across the active backend."""
        return self._run_read(lambda b: b.list_prompts())

    def list_versions(self, prompt_id: str) -> list[str]:
        """Lists SemVer-sorted versions for prompt_id."""
        return self._run_read(lambda b: b.list_versions(prompt_id))

    def delete_prompt(self, prompt_id: str, version: str | None = None) -> bool:
        """Deletes a prompt or specific version from the primary backend."""
        return self._adapter.delete_prompt(prompt_id, version=version)

    def render_prompt(
        self,
        prompt_id: str,
        variables: Mapping[str, Any] | None = None,
        *,
        version: str = "latest",
    ) -> str:
        """Loads prompt_id@version and renders it in SandboxedEnvironment."""
        return _render_sandboxed(
            self.get_prompt(prompt_id, version=version).template, variables
        )

    def format_prompt(
        self,
        template_or_id: str | prompt_ports.PromptTemplate,
        variables: Mapping[str, Any] | None = None,
    ) -> str:
        """Renders a PromptTemplate, registered prompt_id, or raw template."""
        if isinstance(template_or_id, prompt_ports.PromptTemplate):
            return _render_sandboxed(template_or_id.template, variables)
        if not any(ch in template_or_id for ch in ("{", " ", "\n")):
            try:
                cid = yaml_utils.validate_prompt_id(template_or_id)
                return _render_sandboxed(
                    self.get_prompt(cid).template, variables
                )
            except (ValueError, exceptions.PromptNotFoundError):
                pass
        return _render_sandboxed(template_or_id, variables)
