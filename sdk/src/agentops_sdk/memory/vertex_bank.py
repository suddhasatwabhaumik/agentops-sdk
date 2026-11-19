# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Vertex AI Agent Engine Memory Bank adapter for LongTermMemoryPort."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Iterable, Mapping, Sequence
import datetime
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
from agentops_sdk.ports import memory as memory_ports

_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ENG_PAT = r"^projects/[^/\s]+/locations/[^/\s]+/reasoningEngines/[^/\s]+$"
_ENGINE_RE = re.compile(_ENG_PAT)
_META_KEYS = ("bool_value", "double_value", "string_value", "timestamp_value")
validate_user_id = validation.validate_user_id


def _field(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, Mapping) else getattr(obj, key, None)


def _encode_val(val: Any) -> dict[str, Any]:
    if isinstance(val, Mapping) and any(k in val for k in _META_KEYS):
        return dict(val)
    if isinstance(val, bool | datetime.datetime):
        return {
            "bool_value" if isinstance(val, bool) else "timestamp_value": val
        }
    key = "double_value" if isinstance(val, int | float) else "string_value"
    return {key: float(val) if isinstance(val, int | float) else str(val)}


def _decode_val(val: Any) -> Any:
    for item in (val, *(_field(val, k) for k in _META_KEYS)):
        if isinstance(item, datetime.datetime):
            return item.isoformat()
        if isinstance(item, bool | int | float | str):
            return item
    return str(val)


def _to_item(
    raw: Any, uid: str, fact: str = "", mtype: str = "fact", meta: Any = None
) -> models.MemoryItem:
    score = _field(raw, "score")
    if not isinstance(score, int | float) or isinstance(score, bool):
        score = _field(raw, "distance")
    has_score = isinstance(score, int | float) and not isinstance(score, bool)
    inner = raw
    for attr in ("memory", "response"):
        cand = _field(raw, attr)
        if any(isinstance(_field(cand, fld), str) for fld in ("fact", "name")):
            inner = cand
            break
    raw_meta = _field(inner, "metadata")
    pairs = raw_meta.items() if isinstance(raw_meta, Mapping) else ()
    decoded = {str(k): _decode_val(v) for k, v in pairs}
    mt = decoded.pop("memory_type", None)
    rid = _field(inner, "name") or _field(raw, "name")
    rfact, ctime = _field(inner, "fact"), _field(inner, "create_time")
    ts = ctime.isoformat() if isinstance(ctime, datetime.datetime) else ctime
    return models.MemoryItem(
        memory_id=rid if isinstance(rid, str) else "",
        user_id=uid,
        fact=rfact if isinstance(rfact, str) and rfact else fact,
        memory_type=mt if isinstance(mt, str) and mt else mtype,
        score=float(score) if has_score else None,
        created_at=ts if isinstance(ts, str) and ts else models.utc_now_iso(),
        metadata=decoded or dict(meta or {}),
    )


def _parse_purge_count(op: Any) -> int:
    resp = _field(op, "response") or op
    for val in (op, _field(resp, "purge_count"), _field(op, "purge_count")):
        if isinstance(val, int) and not isinstance(val, bool):
            return max(0, val)
    return 0


def _parse_generated(op: Any, uid: str) -> list[models.MemoryItem]:
    resp = _field(op, "response") or op
    rows = _field(resp, "generated_memories")
    if isinstance(rows, str | bytes | Mapping) or not isinstance(
        rows, Iterable
    ):
        rows = op if isinstance(op, Sequence) else ()
    return [item for entry in rows if (item := _to_item(entry, uid)).fact]


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
        cf = fact.strip() if isinstance(fact, str) else ""
        if not cf or (ttl is not None and ttl <= 0):
            raise ValueError("fact must be non-empty and ttl_seconds positive.")
        cfg: dict[str, Any] = {"ttl": f"{ttl}s"} if ttl is not None else {}
        if mtype != "fact" or meta:
            enc = {str(k): _encode_val(v) for k, v in (meta or {}).items()}
            cfg["metadata"] = {**enc, "memory_type": _encode_val(mtype)}
        extra: dict[str, Any] = {"fact": cf, **({"config": cfg} if cfg else {})}
        return self._scope_kw(user_id, **extra)

    def _retrieve_kwargs(
        self, user_id: str, query: str | None, limit: int
    ) -> tuple[str, dict[str, Any]]:
        if limit <= 0:
            raise ValueError("limit must be positive.")
        cq = query.strip() if query else ""
        key = "similarity_search_params" if cq else "simple_retrieval_params"
        p = {"search_query": cq, "top_k": limit} if cq else {"page_size": limit}
        return self._scope_kw(user_id, **{key: p})

    def _generate_kwargs(
        self, turns: Sequence[models.ConversationTurn], user_id: str
    ) -> tuple[str, dict[str, Any]]:
        ev = [
            {"content": {"role": t.role, "parts": [{"text": t.content}]}}
            for t in turns
        ]
        return self._scope_kw(user_id, direct_contents_source={"events": ev})

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
        return _to_item(op, uid, kw["fact"], memory_type, metadata)

    @override
    def retrieve_memories(
        self, *, user_id: str, query: str | None = None, limit: int = 5
    ) -> list[models.MemoryItem]:
        uid, kw = self._retrieve_kwargs(user_id, query, limit)
        try:
            rows = self._client.agent_engines.memories.retrieve(**kw)
            gen = (it for r in rows if (it := _to_item(r, uid)).fact)
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
        return _parse_generated(self._invoke("generate", uid, **kw), uid)

    @override
    def purge_memories(self, *, user_id: str) -> int:
        uid, kw = self._purge_kwargs(user_id)
        return _parse_purge_count(self._invoke("purge", uid, **kw))

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
        return _to_item(op, uid, kw["fact"], memory_type, metadata)

    @override
    async def aretrieve_memories(
        self, *, user_id: str, query: str | None = None, limit: int = 5
    ) -> list[models.MemoryItem]:
        fn = self._aio("retrieve")
        if fn is None:
            return await asyncio.to_thread(
                self.retrieve_memories,
                user_id=user_id,
                query=query,
                limit=limit,
            )
        uid, kw = self._retrieve_kwargs(user_id, query, limit)
        try:
            pages = await fn(**kw)
            if not isinstance(pages, AsyncIterable):
                gen = (it for r in pages if (it := _to_item(r, uid)).fact)
                return list(itertools.islice(gen, limit))
            out: list[models.MemoryItem] = []
            async for raw in pages:
                if (item := _to_item(raw, uid)).fact:
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
        return _parse_generated(await self._ainvoke("generate", uid, **kw), uid)

    @override
    async def apurge_memories(self, *, user_id: str) -> int:
        uid, kw = self._purge_kwargs(user_id)
        return _parse_purge_count(await self._ainvoke("purge", uid, **kw))
