# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Port contracts and template model for versioned prompt backends."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import pydantic


class PromptTemplate(pydantic.BaseModel):
    """Immutable versioned prompt definition."""

    model_config = pydantic.ConfigDict(frozen=True)

    prompt_id: str
    version: str = "1.0.0"
    template: str
    description: str = ""
    model_name: str = "gemini-2.5-flash"
    system_instruction: str | None = None
    variables: tuple[str, ...] = ()
    generation_config: dict[str, Any] = pydantic.Field(default_factory=dict)
    metadata: dict[str, Any] = pydantic.Field(default_factory=dict)


@runtime_checkable
class PromptBackendPort(Protocol):
    """Port contract for storing and retrieving versioned PromptTemplates."""

    def get_prompt(
        self,
        prompt_id: str,
        version: str = "latest",
    ) -> PromptTemplate:
        """Fetches a PromptTemplate by ID and SemVer or 'latest'."""
        ...

    def register_prompt(self, prompt: PromptTemplate) -> PromptTemplate:
        """Persists a new prompt version and returns the stored record."""
        ...

    def list_prompts(self) -> list[str]:
        """Returns sorted prompt IDs available in the backend."""
        ...

    def list_versions(self, prompt_id: str) -> list[str]:
        """Returns SemVer-sorted versions registered for prompt_id."""
        ...

    def delete_prompt(
        self,
        prompt_id: str,
        version: str | None = None,
    ) -> bool:
        """Deletes a prompt or specific version; returns True if deleted."""
        ...
