# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Composite facade coordinating session turns and long-term user memories."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
import logging
from typing import Any

from typing_extensions import override

from agentops_sdk.domain import exceptions
from agentops_sdk.domain import models
from agentops_sdk.memory import _bank_codec as codec
from agentops_sdk.ports import memory as memory_ports

logger = logging.getLogger(__name__)
_TURN_CAP = 200


class CompositeMemory(
    memory_ports.SessionMemoryPort, memory_ports.LongTermMemoryPort
):
    """Unified coordinator over SessionMemoryPort and LongTermMemoryPort."""

    def __init__(
        self,
        session_memory: memory_ports.SessionMemoryPort,
        long_term_memory: memory_ports.LongTermMemoryPort,
    ) -> None:
        """Initializes the composite memory coordinator."""
        if not isinstance(session_memory, memory_ports.SessionMemoryPort):
            raise TypeError("session_memory must satisfy SessionMemoryPort.")
        if not isinstance(long_term_memory, memory_ports.LongTermMemoryPort):
            raise TypeError("long_term_memory must satisfy LongTermMemoryPort.")
        self._session = session_memory
        self._long_term = long_term_memory

    @property
    def session(self) -> memory_ports.SessionMemoryPort:
        """Underlying short-term session memory adapter."""
        return self._session

    @property
    def long_term(self) -> memory_ports.LongTermMemoryPort:
        """Underlying long-term memory adapter."""
        return self._long_term

    def close(self) -> None:
        """Closes underlying memory adapters that expose close()."""
        try:
            if callable(fn := getattr(self._session, "close", None)):
                fn()
        finally:
            if callable(fn := getattr(self._long_term, "close", None)):
                fn()

    def __enter__(self) -> CompositeMemory:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def get_context(
        self,
        session_id: str,
        user_id: str,
        *,
        query: str | None = None,
        turn_limit: int = 20,
        memory_limit: int = 5,
        strict: bool = True,
    ) -> dict[str, Any]:
        """Retrieves recent session turns and relevant long-term memories."""
        turns = self._session.get_messages(session_id, limit=turn_limit)
        try:
            memories = self._long_term.retrieve_memories(
                user_id=user_id, query=query, limit=memory_limit
            )
        except exceptions.AgentOpsError as exc:
            if strict:
                raise
            logger.warning("Recall degraded: %s", type(exc).__name__)
            memories = []
        return codec.build_context(session_id, user_id, turns, memories)

    async def _safe_aretrieve(
        self, uid: str, query: str | None, limit: int, strict: bool
    ) -> list[models.MemoryItem]:
        try:
            return await self._long_term.aretrieve_memories(
                user_id=uid, query=query, limit=limit
            )
        except exceptions.AgentOpsError as exc:
            if strict:
                raise
            logger.warning("Async recall degraded: %s", type(exc).__name__)
            return []

    async def aget_context(
        self,
        session_id: str,
        user_id: str,
        *,
        query: str | None = None,
        turn_limit: int = 20,
        memory_limit: int = 5,
        strict: bool = True,
    ) -> dict[str, Any]:
        """Asynchronously retrieves session turns and long-term memories."""
        turns, memories = await asyncio.gather(
            self._session.aget_messages(session_id, limit=turn_limit),
            self._safe_aretrieve(user_id, query, memory_limit, strict),
        )
        return codec.build_context(session_id, user_id, turns, memories)

    def consolidate_session(
        self, session_id: str, user_id: str, *, clear_after: bool = False
    ) -> list[models.MemoryItem]:
        """Extracts persistent memories from session turns."""
        turns = self._session.get_messages(session_id, limit=_TURN_CAP)
        items = self._long_term.generate_memories(turns, user_id=user_id)
        if clear_after:
            self._session.clear_session(session_id)
        return items

    async def aconsolidate_session(
        self, session_id: str, user_id: str, *, clear_after: bool = False
    ) -> list[models.MemoryItem]:
        """Asynchronously extracts persistent memories from session turns."""
        turns = await self._session.aget_messages(session_id, limit=_TURN_CAP)
        items = await self._long_term.agenerate_memories(turns, user_id=user_id)
        if clear_after:
            await self._session.aclear_session(session_id)
        return items

    @override
    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> models.ConversationTurn:
        return self._session.add_message(session_id, role, content, metadata)

    @override
    def get_messages(
        self,
        session_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        after_id: int | None = None,
        before_id: int | None = None,
    ) -> list[models.ConversationTurn]:
        return self._session.get_messages(
            session_id,
            limit=limit,
            offset=offset,
            after_id=after_id,
            before_id=before_id,
        )

    @override
    def clear_session(self, session_id: str) -> int:
        return self._session.clear_session(session_id)

    @override
    async def aadd_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> models.ConversationTurn:
        fn = self._session.aadd_message
        return await fn(session_id, role, content, metadata)

    @override
    async def aget_messages(
        self,
        session_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        after_id: int | None = None,
        before_id: int | None = None,
    ) -> list[models.ConversationTurn]:
        return await self._session.aget_messages(
            session_id,
            limit=limit,
            offset=offset,
            after_id=after_id,
            before_id=before_id,
        )

    @override
    async def aclear_session(self, session_id: str) -> int:
        return await self._session.aclear_session(session_id)

    @override
    def create_memory(
        self,
        fact: str,
        *,
        user_id: str,
        memory_type: str = "fact",
        metadata: Mapping[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> models.MemoryItem:
        return self._long_term.create_memory(
            fact,
            user_id=user_id,
            memory_type=memory_type,
            metadata=metadata,
            ttl_seconds=ttl_seconds,
        )

    @override
    def retrieve_memories(
        self,
        *,
        user_id: str,
        query: str | None = None,
        limit: int = 5,
        memory_type: str | None = None,
    ) -> list[models.MemoryItem]:
        fn = self._long_term.retrieve_memories
        return fn(
            user_id=user_id, query=query, limit=limit, memory_type=memory_type
        )

    @override
    def generate_memories(
        self, turns: Sequence[models.ConversationTurn], *, user_id: str
    ) -> list[models.MemoryItem]:
        return self._long_term.generate_memories(turns, user_id=user_id)

    @override
    def purge_memories(self, *, user_id: str) -> int:
        return self._long_term.purge_memories(user_id=user_id)

    @override
    async def acreate_memory(
        self,
        fact: str,
        *,
        user_id: str,
        memory_type: str = "fact",
        metadata: Mapping[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> models.MemoryItem:
        return await self._long_term.acreate_memory(
            fact,
            user_id=user_id,
            memory_type=memory_type,
            metadata=metadata,
            ttl_seconds=ttl_seconds,
        )

    @override
    async def aretrieve_memories(
        self,
        *,
        user_id: str,
        query: str | None = None,
        limit: int = 5,
        memory_type: str | None = None,
    ) -> list[models.MemoryItem]:
        fn = self._long_term.aretrieve_memories
        return await fn(
            user_id=user_id, query=query, limit=limit, memory_type=memory_type
        )

    @override
    async def agenerate_memories(
        self, turns: Sequence[models.ConversationTurn], *, user_id: str
    ) -> list[models.MemoryItem]:
        return await self._long_term.agenerate_memories(turns, user_id=user_id)

    @override
    async def apurge_memories(self, *, user_id: str) -> int:
        return await self._long_term.apurge_memories(user_id=user_id)
