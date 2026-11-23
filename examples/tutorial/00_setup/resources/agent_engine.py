"""Vertex AI Agent Engine instance used for Memory Bank (tutorial 12).

No code is deployed to it; it acts as the Memory Bank container. Copy its
resource name into MEMORY_BANK_NAME in .env.
"""

from __future__ import annotations

import vertexai

DISPLAY_NAME = "agentops-tutorial-memory"


def ensure(project: str, location: str) -> str:
    client = vertexai.Client(project=project, location=location)
    for engine in client.agent_engines.list():
        resource = getattr(engine, "api_resource", engine)
        if getattr(resource, "display_name", "") == DISPLAY_NAME:
            return f"exists: {resource.name}"
    # Empty memory_bank_config enables Memory Bank with default service
    # generation and embedding models.
    engine = client.agent_engines.create(
        config={
            "display_name": DISPLAY_NAME,
            "description": (
                "Memory Bank for AgentOps SDK tutorials (no deployed code)."
            ),
            "context_spec": {"memory_bank_config": {}},
        }
    )
    resource = getattr(engine, "api_resource", engine)
    return f"created: {resource.name}"
