"""One real Gemini call on Vertex AI, used by the tutorials that need an LLM.

Returns the raw `GenerateContentResponse`, because @trace_llm_call reads
`response.usage_metadata` (token counts) and `response.model_version` from it.
"""

from __future__ import annotations

import os
from typing import Any

from google import genai
from google.genai import types

_client: genai.Client | None = None


def client() -> genai.Client:
    """Vertex AI backend, Application Default Credentials, region from .env."""
    global _client
    if _client is None:
        _client = genai.Client(
            vertexai=True,
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.getenv(
                "GEMINI_LOCATION",
                os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west4"),
            ),
        )
    return _client


def model_name() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def generate(
    prompt: str, *, system: str | None = None, response_schema: Any = None
) -> Any:
    """Single-turn generation; `response_schema` enforces JSON output."""
    config = types.GenerateContentConfig(
        system_instruction=system,
        temperature=0.2,
        max_output_tokens=512,
        response_mime_type="application/json" if response_schema else None,
        response_schema=response_schema,
    )
    return client().models.generate_content(
        model=model_name(), contents=prompt, config=config
    )
