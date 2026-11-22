# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Vertex AI Agent Engine Memory Bank adapter for LongTermMemoryPort."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Mapping, Sequence
import inspect
import itertools
import re
from typing import Any

from typing_extensions import override
import vertexai

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import models
from agentops_sdk.domain import validation
from agentops_sdk.memory import _bank_codec as codec
from agentops_sdk.ports import memory as memory_ports

_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ENG_PAT = r"^projects/[^/\s]+/locations/[^/\s]+/reasoningEngines/[^/\s]+$"
_ENGINE_RE = re.compile(_ENG_PAT)
validate_user_id = validation.validate_user_id


class VertexMemoryBank(memory_ports.LongTermMemoryPort):
    """Vertex AI Memory Bank adapter implementing LongTermMemoryPort."""

    def __init__(
        self,
        reasoning_engine_name: str,
        *,
        project_id: str | None = None,
        location: str | None = None,
        client: Any | None = None,
    ) -> None:
        """Initializes the Vertex AI Memory Bank adapter."""
        proj, loc = settings.resolve_gcp_project_and_location(
            project_id, location, require_project=client is None
        )
        name = reasoning_engine_name
        raw = name.strip() if isinstance(name, str) else ""
        if _BARE_ID_RE.fullmatch(raw) and proj and loc:
            raw = f"projects/{proj}/locations/{loc}/reasoningEngines/{raw}"
        if ".." in raw or not _ENGINE_RE.fullmatch(raw):
            raise ValueError(f"Invalid reasoning_engine_name: {name!r}")
        self.engine_name = raw
        self._client = client or vertexai.Client(project=proj, location=loc)

    def _aio(self, method: str) -> Any | None:
        ae = getattr(getattr(self._client, "aio", None), "agent_engines", None)
        fn = getattr(getattr(ae, "memories", None), method, None)
        return fn if inspect.iscoroutinefunction(fn) else None

    def _scope_kw(
        self, user_id: str, **extra: Any
    ) -> tuple[str, dict[str, Any]]:
        uid = validate_user_id(user_id)
        kw = {"name": self.engine_name, "scope": {"user_id": uid}, **extra}
        return uid, kw

    def _create_kwargs(
        self, fact: str, user_id: str, mtype: str, meta: Any, ttl: int | None
    ) -> tuple[str, dict[str, Any]]:
        clean_fact = fact.strip() if isinstance(fact, str) else ""
        if not clean_fact or (ttl is not None and ttl <= 0):
            raise ValueError("fact must be non-empty and ttl_seconds positive.")
        cfg: dict[str, Any] = {"ttl": f"{ttl}s"} if ttl is not None else {}
        if mtype != "fact" or meta:
            enc = {str(k): codec.encode_val(v) for k, v in (meta or {}).items()}
            cfg["metadata"] = {**enc, "memory_type": codec.encode_val(mtype)}
        extra: dict[str, Any] = {
            "fact": clean_fact,
            **({"config": cfg} if cfg else {}),
        }
        return self._scope_kw(user_id, **extra)

    def _retrieve_kwargs(
        self, uid: str, query: str | None, limit: int, mtype: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        if limit <= 0:
            raise ValueError("limit must be positive.")
        filter_type = codec.clean_memory_type(mtype)
        fetch_limit = (
            min(max(limit * 4, 20), 100) if filter_type is not None else limit
        )
        clean_query = query.strip() if query else ""
        key = (
            "similarity_search_params"
            if clean_query
            else "simple_retrieval_params"
        )
        params = (
            {"search_query": clean_query, "top_k": fetch_limit}
            if clean_query
            else {"page_size": fetch_limit}
        )
        return self._scope_kw(uid, **{key: params})

    def _generate_kwargs(
        self, turns: Sequence[models.ConversationTurn], user_id: str
    ) -> tuple[str, dict[str, Any]]:
        events = [
            {"content": {"role": t.role, "parts": [{"text": t.content}]}}
            for t in turns
        ]
        return self._scope_kw(
            user_id, direct_contents_source={"events": events}
        )

    def _purge_kwargs(self, user_id: str) -> tuple[str, dict[str, Any]]:
        uid = validate_user_id(user_id)
        flt = f'scope.user_id="{uid}"'
        return uid, {"name": self.engine_name, "filter": flt, "force": True}

    def _invoke(self, method: str, uid: str, **kw: Any) -> Any:
        try:
            return getattr(self._client.agent_engines.memories, method)(**kw)
        except Exception as exc:
            raise exceptions.MemoryBankError(f"{method} failed: {uid}") from exc

    async def _ainvoke(self, method: str, uid: str, **kw: Any) -> Any:
        fn = self._aio(method)
        try:
            if fn is not None:
                return await fn(**kw)
            sync_fn = getattr(self._client.agent_engines.memories, method)
            return await asyncio.to_thread(sync_fn, **kw)
        except Exception as exc:
            raise exceptions.MemoryBankError(f"{method} failed: {uid}") from exc

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
        uid, kw = self._create_kwargs(
            fact, user_id, memory_type, metadata, ttl_seconds
        )
        op = self._invoke("create", uid, **kw)
        return codec.to_item(op, uid, kw["fact"], memory_type, metadata)

    @override
    def retrieve_memories(
        self,
        *,
        user_id: str,
        query: str | None = None,
        limit: int = 5,
        memory_type: str | None = None,
    ) -> list[models.MemoryItem]:
        filter_type = codec.clean_memory_type(memory_type)
        uid, kw = self._retrieve_kwargs(user_id, query, limit, filter_type)
        try:
            rows = self._client.agent_engines.memories.retrieve(**kw)
            gen = (
                item
                for row in rows
                if codec.keep_memory_item(
                    item := codec.to_item(row, uid), filter_type
                )
            )
            return list(itertools.islice(gen, limit))
        except Exception as exc:
            raise exceptions.MemoryBankError(f"retrieve failed: {uid}") from exc

    @override
    def generate_memories(
        self, turns: Sequence[models.ConversationTurn], *, user_id: str
    ) -> list[models.MemoryItem]:
        uid, kw = self._generate_kwargs(turns, user_id)
        if not turns:
            return []
        return codec.parse_generated(self._invoke("generate", uid, **kw), uid)

    @override
    def purge_memories(self, *, user_id: str) -> int:
        uid, kw = self._purge_kwargs(user_id)
        return codec.parse_purge_count(self._invoke("purge", uid, **kw))

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
        uid, kw = self._create_kwargs(
            fact, user_id, memory_type, metadata, ttl_seconds
        )
        op = await self._ainvoke("create", uid, **kw)
        return codec.to_item(op, uid, kw["fact"], memory_type, metadata)

    @override
    async def aretrieve_memories(
        self,
        *,
        user_id: str,
        query: str | None = None,
        limit: int = 5,
        memory_type: str | None = None,
    ) -> list[models.MemoryItem]:
        if (fn := self._aio("retrieve")) is None:
            kw_s: dict[str, Any] = {
                "user_id": user_id,
                "query": query,
                "limit": limit,
                "memory_type": memory_type,
            }
            return await asyncio.to_thread(self.retrieve_memories, **kw_s)
        filter_type = codec.clean_memory_type(memory_type)
        uid, kw = self._retrieve_kwargs(user_id, query, limit, filter_type)
        try:
            pages = await fn(**kw)
            if not isinstance(pages, AsyncIterable):
                gen = (
                    item
                    for row in pages
                    if codec.keep_memory_item(
                        item := codec.to_item(row, uid), filter_type
                    )
                )
                return list(itertools.islice(gen, limit))
            out: list[models.MemoryItem] = []
            async for raw in pages:
                if codec.keep_memory_item(
                    item := codec.to_item(raw, uid), filter_type
                ):
                    out.append(item)
                if len(out) >= limit:
                    break
            return out
        except Exception as exc:
            raise exceptions.MemoryBankError(f"retrieve failed: {uid}") from exc

    @override
    async def agenerate_memories(
        self, turns: Sequence[models.ConversationTurn], *, user_id: str
    ) -> list[models.MemoryItem]:
        uid, kw = self._generate_kwargs(turns, user_id)
        if not turns:
            return []
        return codec.parse_generated(
            await self._ainvoke("generate", uid, **kw), uid
        )

    @override
    async def apurge_memories(self, *, user_id: str) -> int:
        uid, kw = self._purge_kwargs(user_id)
        return codec.parse_purge_count(await self._ainvoke("purge", uid, **kw))
