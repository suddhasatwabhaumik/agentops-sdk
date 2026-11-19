#!/usr/bin/env python3
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""ADK Prompt Registry Agent exercising v1 LocalYAML and Vertex AI backends."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any

from agentops_sdk import LocalYAMLPromptAdapter
from agentops_sdk import PromptRegistry
from agentops_sdk import VertexAIPromptAdapter

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("adk-prompt-registry-agent")


class ADKPromptRegistryAgent:
    """Agent using version-controlled prompts via explicit v1 adapters."""

    def __init__(
        self,
        backend: str = "local",
        project_id: str = "",
        location: str = "europe-west4",
        local_dir: str = "prompt_examples",
    ) -> None:
        self.backend = backend
        self.project_id = project_id
        self.location = location
        self.local_dir = local_dir
        local_adapter = LocalYAMLPromptAdapter(local_dir=local_dir)
        if backend == "vertex_ai":
            if not project_id:
                raise ValueError("vertex_ai backend requires --project-id.")
            adapter: Any = VertexAIPromptAdapter(
                project_id=project_id, location=location
            )
            self.registry = PromptRegistry(
                backend="vertex_ai",
                adapter=adapter,
                fallback_adapter=local_adapter,
                fallback_to_local=True,
            )
        else:
            self.registry = PromptRegistry(
                backend="local", adapter=local_adapter
            )

    def _discover_and_render(self) -> tuple[int, str, str]:
        prompts = self.registry.list_prompts()
        target = "sql_agent_prompt"
        tpl = self.registry.get_prompt(target, version="latest")
        logger.info("Prompt %s v%s loaded", tpl.prompt_id, tpl.version)
        variables = {
            "dialect": "GoogleSQL",
            "schema_context": "TABLE telemetry (id INT64, cost FLOAT64)",
            "user_query": "Compute mean token cost.",
            "customer_name": "Acme Agricultural",
            "product_line": "Combines & Harvesting",
            "budget": "$500,000",
        }
        rendered = self.registry.render_prompt(target, variables=variables)
        return len(prompts), target, rendered[:120]

    def _register_and_verify_semver(self) -> None:
        ephemeral_id = f"e2e_agent_eval_{int(time.time())}"
        self.registry.register_prompt(
            prompt_id=ephemeral_id,
            template="E2E v1: {{task}} timeout={{timeout}}s.",
            version="1.0.0",
            description="Ephemeral v1",
            variables=["task", "timeout"],
        )
        self.registry.register_prompt(
            prompt_id=ephemeral_id,
            template=(
                "E2E v2: {{task}} timeout={{timeout}}s trace={{trace_id}}."
            ),
            version="2.0.0",
            description="Ephemeral v2",
            variables=["task", "timeout", "trace_id"],
        )
        latest = self.registry.render_prompt(
            ephemeral_id,
            {"task": "PromptOps", "timeout": 30, "trace_id": "tr-001"},
            version="latest",
        )
        assert "E2E v2" in latest and "tr-001" in latest
        v1 = self.registry.render_prompt(
            ephemeral_id, {"task": "PromptOps", "timeout": 30}, version="1.0.0"
        )
        assert "E2E v1" in v1
        self.registry.delete_prompt(ephemeral_id)

    def run_e2e_lifecycle(self) -> dict[str, Any]:
        """Run discovery, rendering, SemVer resolution, and cleanup."""
        count, target, preview = self._discover_and_render()
        self._register_and_verify_semver()
        return {
            "backend": self.registry.backend,
            "project_id": self.project_id,
            "listed_prompts_count": count,
            "retrieved_prompt_id": target,
            "rendered_prompt_sample": preview,
            "semver_resolution_success": True,
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend", default="local", choices=("local", "vertex_ai")
    )
    parser.add_argument(
        "--project-id",
        default=os.getenv("AGENTOPS_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT", ""),
    )
    parser.add_argument(
        "--location",
        default=os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west4"),
    )
    parser.add_argument(
        "--local-dir",
        default=str(Path(__file__).resolve().parent / "prompt_examples"),
    )
    return parser.parse_args()


def main() -> None:
    """Execute Prompt Registry E2E validation without dirtying repo files."""
    args = _parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / "prompts"
        shutil.copytree(args.local_dir, staged)
        agent = ADKPromptRegistryAgent(
            backend=args.backend,
            project_id=args.project_id,
            location=args.location,
            local_dir=str(staged),
        )
        report = agent.run_e2e_lifecycle()
        print("E2E Prompt Registry Summary:", report)
        assert report["semver_resolution_success"] is True
        sys.exit(0)


if __name__ == "__main__":
    main()
