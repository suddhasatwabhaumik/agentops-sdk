"""A Vertex AI Agent Engine instance used only for its Memory Bank (tutorial 12).

No code is deployed to it; it is the container for memories. Its resource name goes into
MEMORY_BANK_NAME in .env.
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
    # An empty memory_bank_config turns the Memory Bank on with the service's default
    # generation and embedding models (the API rejects older Gemini versions here).
    engine = client.agent_engines.create(
        config={
            "display_name": DISPLAY_NAME,
            "description": "Memory Bank for the AgentOps SDK tutorials (no deployed agent code).",
            "context_spec": {"memory_bank_config": {}},
        }
    )
    resource = getattr(engine, "api_resource", engine)
    return f"created: {resource.name}"
