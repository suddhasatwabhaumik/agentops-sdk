# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Port contracts for conversational session and long-term memory backends."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from agentops_sdk.domain import models


@runtime_checkable
class SessionMemoryPort(Protocol):
    """Port contract for short-term conversational session turn persistence."""

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> models.ConversationTurn:
        """Appends a message turn to the specified session."""
        ...

    def get_messages(
        self,
        session_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        after_id: int | None = None,
        before_id: int | None = None,
    ) -> list[models.ConversationTurn]:
        """Retrieves ordered turns for a session via cursor or offset."""
        ...

    def clear_session(self, session_id: str) -> int:
        """Deletes all turns belonging to session_id and returns count."""
        ...

    async def aadd_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> models.ConversationTurn:
        """Asynchronously appends a message turn to the specified session."""
        ...

    async def aget_messages(
        self,
        session_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        after_id: int | None = None,
        before_id: int | None = None,
    ) -> list[models.ConversationTurn]:
        """Asynchronously retrieves ordered turns for a session."""
        ...

    async def aclear_session(self, session_id: str) -> int:
        """Asynchronously deletes all turns belonging to session_id."""
        ...


@runtime_checkable
class LongTermMemoryPort(Protocol):
    """Port contract for cross-session semantic memory storage and recall."""

    def create_memory(
        self,
        fact: str,
        *,
        user_id: str,
        memory_type: str = "fact",
        metadata: Mapping[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> models.MemoryItem:
        """Stores a persistent fact scoped to user_id."""
        ...

    def retrieve_memories(
        self,
        *,
        user_id: str,
        query: str | None = None,
        limit: int = 5,
        memory_type: str | None = None,
    ) -> list[models.MemoryItem]:
        """Retrieves memories for user_id ranked by semantic relevance."""
        ...

    def generate_memories(
        self,
        turns: Sequence[models.ConversationTurn],
        *,
        user_id: str,
    ) -> list[models.MemoryItem]:
        """Extracts and consolidates persistent memories from session turns."""
        ...

    def purge_memories(self, *, user_id: str) -> int:
        """Purges all stored memories for user_id."""
        ...

    async def acreate_memory(
        self,
        fact: str,
        *,
        user_id: str,
        memory_type: str = "fact",
        metadata: Mapping[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> models.MemoryItem:
        """Asynchronously stores a persistent fact scoped to user_id."""
        ...

    async def aretrieve_memories(
        self,
        *,
        user_id: str,
        query: str | None = None,
        limit: int = 5,
        memory_type: str | None = None,
    ) -> list[models.MemoryItem]:
        """Asynchronously retrieves memories for user_id."""
        ...

    async def agenerate_memories(
        self,
        turns: Sequence[models.ConversationTurn],
        *,
        user_id: str,
    ) -> list[models.MemoryItem]:
        """Asynchronously extracts and consolidates memories from turns."""
        ...

    async def apurge_memories(self, *, user_id: str) -> int:
        """Asynchronously purges all stored memories for user_id."""
        ...
