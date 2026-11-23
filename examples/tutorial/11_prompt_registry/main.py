# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Tutorial 11: version prompts in Vertex AI Prompt Management."""

from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared import gemini
from _shared import settings
from _shared import telemetry

cfg = settings.load("11_prompt_registry")
tel = telemetry.start(cfg)

from agentops_sdk import PromptRegistry
from agentops_sdk import VertexAIPromptAdapter
from agentops_sdk import trace_agent_flow
from agentops_sdk import trace_llm_call

PROMPT_ID = "agentops_tutorial_parts_advisor"


@trace_llm_call(
    kpi_id="tenant-parts-availability",
    pl_impact_line="aftersales-opex",
)
def ask(prompt: str, system: str | None):
    """Invoke Gemini using the rendered template and system instruction."""
    return gemini.generate(prompt, system=system)


def _register_sample_versions(registry: PromptRegistry) -> None:
    registry.register_prompt(
        PROMPT_ID,
        version="1.0.0",
        template=(
            "You are an enterprise parts advisor supporting tenant "
            "{{tenant_id}} for a Google Customer. Question: {{question}}"
        ),
        variables=["tenant_id", "question"],
        description="first wording",
    )
    registry.register_prompt(
        PROMPT_ID,
        version="1.1.0",
        template=(
            "You are an enterprise parts advisor supporting tenant "
            "{{tenant_id}} in {{region}} for a Google Customer.\n"
            "Answer in two sentences. Question: {{question}}"
        ),
        variables=["tenant_id", "region", "question"],
        description="adds region and length limit",
        system_instruction="Never invent part numbers.",
        model_name=gemini.model_name(),
    )


@trace_agent_flow(
    flow_name="prompt_registry_demo",
    kpi_id="tenant-parts-availability",
    pl_impact_line="aftersales-opex",
)
def main() -> None:
    """Register SemVer prompt versions in Vertex AI and render latest."""
    location = os.getenv("PROMPT_REGISTRY_LOCATION", cfg.location)
    adapter = VertexAIPromptAdapter(project_id=cfg.project, location=location)
    registry = PromptRegistry(
        backend="vertex_ai", adapter=adapter, fallback_to_local=False
    )
    print(f"\nbackend: Vertex AI ({cfg.project}, {location})")

    _register_sample_versions(registry)
    versions = registry.list_versions(PROMPT_ID)
    print(f"versions in Vertex AI for {PROMPT_ID}: {versions}")

    tpl = registry.get_prompt(PROMPT_ID, version="latest")
    rendered = registry.render_prompt(
        PROMPT_ID,
        {
            "tenant_id": "tenant-4711",
            "region": "Germany",
            "question": "Which oil filter fits a Series-700?",
        },
    )
    print(f"latest={tpl.version} | rendered:\n   {rendered!r}")
    answer = ask(rendered, system=tpl.system_instruction).text
    print(f"\nGemini: {answer.strip()}")


if __name__ == "__main__":
    try:
        main()
    finally:
        tel.flush()
