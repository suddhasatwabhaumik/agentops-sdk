# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Thread-safe deterministic in-memory adapters for session and user memory."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
import threading
import time
from typing import Any

from typing_extensions import override

from agentops_sdk.domain import models
from agentops_sdk.domain import validation
from agentops_sdk.ports import memory as memory_ports

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _clean_sid(session_id: str) -> str:
    cleaned = session_id.strip() if isinstance(session_id, str) else ""
    if not cleaned or len(cleaned) > 256 or "\x00" in cleaned:
        raise ValueError("session_id must be 1-256 non-null characters.")
    return cleaned


def _score_fact(fact: str, query_lower: str, query_tokens: set[str]) -> float:
    fact_lower = fact.lower()
    if query_tokens:
        overlap = len(query_tokens & set(_TOKEN_RE.findall(fact_lower)))
        if overlap > 0:
            return round(overlap / len(query_tokens), 4)
    return 0.5 if query_lower in fact_lower else 0.0


class InMemorySessionMemory(memory_ports.SessionMemoryPort):
    """Thread-safe in-memory implementation of SessionMemoryPort."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_id = 1
        self._sessions: dict[str, list[models.ConversationTurn]] = {}

    @override
    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> models.ConversationTurn:
        clean_sid = _clean_sid(session_id)
        clean_role = role.strip() if isinstance(role, str) else ""
        if (
            not clean_role
            or len(clean_role) > 64
            or not isinstance(content, str)
        ):
            raise ValueError("role (1-64 chars) and str content are required.")
        with self._lock:
            turn = models.ConversationTurn(
                id=self._next_id,
                session_id=clean_sid,
                role=clean_role,
                content=content,
                metadata=dict(metadata or {}),
            )
            self._next_id += 1
            self._sessions.setdefault(clean_sid, []).append(turn)
            return turn

    @override
    def get_messages(
        self,
        session_id: str,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        after_id: int | None = None,
        before_id: int | None = None,
    ) -> list[models.ConversationTurn]:
        clean_sid = _clean_sid(session_id)
        if limit <= 0 or offset < 0:
            raise ValueError("limit must be positive and offset non-negative.")
        with self._lock:
            turns = list(self._sessions.get(clean_sid, ()))
        if after_id is not None:
            turns = [t for t in turns if t.id is not None and t.id > after_id]
        if before_id is not None:
            turns = [t for t in turns if t.id is not None and t.id < before_id]
        return turns[offset : offset + min(limit, MAX_PAGE_SIZE)]

    @override
    def clear_session(self, session_id: str) -> int:
        clean_sid = _clean_sid(session_id)
        with self._lock:
            return len(self._sessions.pop(clean_sid, []))

    @override
    async def aadd_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> models.ConversationTurn:
        return self.add_message(session_id, role, content, metadata)

    @override
    async def aget_messages(
        self,
        session_id: str,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        after_id: int | None = None,
        before_id: int | None = None,
    ) -> list[models.ConversationTurn]:
        return self.get_messages(
            session_id,
            limit=limit,
            offset=offset,
            after_id=after_id,
            before_id=before_id,
        )

    @override
    async def aclear_session(self, session_id: str) -> int:
        return self.clear_session(session_id)


class InMemoryLongTermMemory(memory_ports.LongTermMemoryPort):
    """Thread-safe in-memory implementation of LongTermMemoryPort."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_id = 1
        self._records: dict[
            str, list[tuple[models.MemoryItem, float | None]]
        ] = {}

    def _active_items(self, valid_uid: str) -> list[models.MemoryItem]:
        now = time.monotonic()
        with self._lock:
            entries = self._records.get(valid_uid, [])
            kept = [
                (it, exp) for it, exp in entries if exp is None or now < exp
            ]
            if len(kept) != len(entries):
                self._records[valid_uid] = kept
            return [it for it, _ in kept]

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
        valid_uid = validation.validate_user_id(user_id)
        clean_fact = fact.strip() if isinstance(fact, str) else ""
        if not clean_fact or (ttl_seconds is not None and ttl_seconds <= 0):
            raise ValueError("fact must be non-empty and ttl_seconds positive.")
        exp = (
            time.monotonic() + ttl_seconds if ttl_seconds is not None else None
        )
        with self._lock:
            item = models.MemoryItem(
                memory_id=f"mem-{self._next_id}",
                user_id=valid_uid,
                fact=clean_fact,
                memory_type=memory_type,
                metadata=dict(metadata or {}),
            )
            self._next_id += 1
            self._records.setdefault(valid_uid, []).append((item, exp))
            return item

    @override
    def retrieve_memories(
        self, *, user_id: str, query: str | None = None, limit: int = 5
    ) -> list[models.MemoryItem]:
        valid_uid = validation.validate_user_id(user_id)
        if limit <= 0:
            raise ValueError("limit must be positive.")
        items = self._active_items(valid_uid)
        if not query or not query.strip():
            return items[:limit]
        q_lower = query.strip().lower()
        q_tokens = set(_TOKEN_RE.findall(q_lower))
        scored = [
            (sc, idx, it.model_copy(update={"score": sc}))
            for idx, it in enumerate(items)
            if (sc := _score_fact(it.fact, q_lower, q_tokens)) > 0.0
        ]
        scored.sort(key=lambda entry: (-entry[0], entry[1]))
        return [entry[2] for entry in scored[:limit]]

    @override
    def generate_memories(
        self, turns: Sequence[models.ConversationTurn], *, user_id: str
    ) -> list[models.MemoryItem]:
        valid_uid = validation.validate_user_id(user_id)
        if not turns:
            return []
        cands = [
            t for t in turns if t.role.lower() == "user" and t.content.strip()
        ]
        if not cands:
            cands = [t for t in turns if t.content.strip()]
        seen: set[str] = set()
        created: list[models.MemoryItem] = []
        for turn in cands:
            text = turn.content.strip()
            if text not in seen:
                seen.add(text)
                meta = {"consolidated": True, "session_id": turn.session_id}
                created.append(
                    self.create_memory(text, user_id=valid_uid, metadata=meta)
                )
        return created

    @override
    def purge_memories(self, *, user_id: str) -> int:
        valid_uid = validation.validate_user_id(user_id)
        now = time.monotonic()
        with self._lock:
            removed = self._records.pop(valid_uid, [])
        return sum(1 for _, exp in removed if exp is None or now < exp)

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
        return self.create_memory(
            fact,
            user_id=user_id,
            memory_type=memory_type,
            metadata=metadata,
            ttl_seconds=ttl_seconds,
        )

    @override
    async def aretrieve_memories(
        self, *, user_id: str, query: str | None = None, limit: int = 5
    ) -> list[models.MemoryItem]:
        return self.retrieve_memories(user_id=user_id, query=query, limit=limit)

    @override
    async def agenerate_memories(
        self, turns: Sequence[models.ConversationTurn], *, user_id: str
    ) -> list[models.MemoryItem]:
        return self.generate_memories(turns, user_id=user_id)

    @override
    async def apurge_memories(self, *, user_id: str) -> int:
        return self.purge_memories(user_id=user_id)
