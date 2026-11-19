# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""LSP contract tests for SessionMemoryPort and LongTermMemoryPort."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from agentops_sdk.domain import models
from agentops_sdk.memory import CloudSQLSessionMemory
from agentops_sdk.memory import CompositeMemory
from agentops_sdk.memory import InMemoryLongTermMemory
from agentops_sdk.memory import InMemorySessionMemory
from agentops_sdk.memory import VertexMemoryBank
from agentops_sdk.ports import memory as memory_ports

_ENGINE = "projects/test-proj/locations/europe-west4/reasoningEngines/engine-01"


class _FakeMemoriesAPI:
    """In-memory fake matching Vertex AI agent_engines.memories."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.items: dict[str, list[SimpleNamespace]] = {}
        self._seq = 0

    def create(self, **kw: Any) -> SimpleNamespace:
        self.calls.append(("create", kw))
        uid = kw["scope"]["user_id"]
        self._seq += 1
        meta = dict((kw.get("config") or {}).get("metadata") or {})
        rec = SimpleNamespace(
            name=f"{_ENGINE}/memories/{self._seq}",
            fact=kw["fact"],
            metadata=meta,
            score=1.0,
        )
        self.items.setdefault(uid, []).append(rec)
        return rec

    def retrieve(self, **kw: Any) -> list[SimpleNamespace]:
        self.calls.append(("retrieve", kw))
        uid = kw["scope"]["user_id"]
        rows = list(self.items.get(uid, ()))
        sim = kw.get("similarity_search_params")
        if sim:
            q = sim["search_query"].lower()
            rows = [r for r in rows if q in r.fact.lower()]
            return rows[: sim["top_k"]]
        lim = kw["simple_retrieval_params"]["page_size"]
        return rows[:lim]

    def generate(self, **kw: Any) -> SimpleNamespace:
        self.calls.append(("generate", kw))
        uid = kw["scope"]["user_id"]
        events = kw["direct_contents_source"]["events"]
        gen = [
            self.create(
                name=kw["name"],
                fact=e["content"]["parts"][0]["text"],
                scope={"user_id": uid},
            )
            for e in events
            if e["content"]["role"] == "user"
        ]
        return SimpleNamespace(response=SimpleNamespace(generated_memories=gen))

    def purge(self, **kw: Any) -> int:
        self.calls.append(("purge", kw))
        uid = kw["filter"].split('"', 2)[1]
        return len(self.items.pop(uid, []))


@pytest.fixture(params=["in_memory", "cloud_sql", "composite"])
def session_adapter(
    request: pytest.FixtureRequest,
) -> Iterator[memory_ports.SessionMemoryPort]:
    """Provides SessionMemoryPort implementations under identical contract."""
    if request.param == "in_memory":
        yield InMemorySessionMemory()
        return
    if request.param == "composite":
        comp = CompositeMemory(
            InMemorySessionMemory(), InMemoryLongTermMemory()
        )
        try:
            yield comp
        finally:
            comp.close()
        return
    adapter = CloudSQLSessionMemory("sqlite:///:memory:")
    try:
        yield adapter
    finally:
        adapter.close()


@pytest.fixture(params=["in_memory", "vertex_bank", "composite"])
def long_term_adapter(
    request: pytest.FixtureRequest,
) -> Iterator[memory_ports.LongTermMemoryPort]:
    """Provides LongTermMemoryPort implementations under identical contract."""
    if request.param == "in_memory":
        yield InMemoryLongTermMemory()
        return
    if request.param == "composite":
        comp = CompositeMemory(
            InMemorySessionMemory(), InMemoryLongTermMemory()
        )
        try:
            yield comp
        finally:
            comp.close()
        return
    fake_api = _FakeMemoriesAPI()
    client = SimpleNamespace(agent_engines=SimpleNamespace(memories=fake_api))
    yield VertexMemoryBank(_ENGINE, client=client)


def test_session_protocol_and_order_limit(
    session_adapter: memory_ports.SessionMemoryPort,
) -> None:
    assert isinstance(session_adapter, memory_ports.SessionMemoryPort)
    t1 = session_adapter.add_message("s-1", "user", "first", {"k": 1})
    t2 = session_adapter.add_message("s-1", "assistant", "second")
    t3 = session_adapter.add_message("s-1", "user", "third")
    assert [t.content for t in session_adapter.get_messages("s-1")] == [
        "first",
        "second",
        "third",
    ]
    assert t1.metadata == {"k": 1}
    limited = session_adapter.get_messages("s-1", limit=2, offset=1)
    assert [t.content for t in limited] == ["second", "third"]
    assert t1.id is not None and t2.id is not None and t3.id is not None
    assert [
        t.content for t in session_adapter.get_messages("s-1", after_id=t1.id)
    ] == ["second", "third"]
    assert [
        t.content for t in session_adapter.get_messages("s-1", before_id=t3.id)
    ] == ["first", "second"]
    assert [
        t.content
        for t in session_adapter.get_messages(
            "s-1", after_id=t1.id, before_id=t3.id
        )
    ] == ["second"]


def test_session_clear_and_validation(
    session_adapter: memory_ports.SessionMemoryPort,
) -> None:
    session_adapter.add_message("s-a", "user", "hello")
    session_adapter.add_message("s-b", "user", "keep")
    assert session_adapter.clear_session("s-a") == 1
    assert session_adapter.get_messages("s-a") == []
    assert len(session_adapter.get_messages("s-b")) == 1
    for bad_sid in ("", "   ", "bad\x00sid"):
        with pytest.raises(ValueError):
            session_adapter.add_message(bad_sid, "user", "msg")
    for bad_lim in (0, -1):
        with pytest.raises(ValueError):
            session_adapter.get_messages("s-b", limit=bad_lim)
    with pytest.raises(ValueError):
        session_adapter.get_messages("s-b", offset=-1)


async def test_session_async_parity(
    session_adapter: memory_ports.SessionMemoryPort,
) -> None:
    await session_adapter.aadd_message("s-async", "user", "ping", {"turn": 1})
    msgs = await session_adapter.aget_messages("s-async")
    assert len(msgs) == 1 and msgs[0].content == "ping"
    assert msgs[0].metadata == {"turn": 1}
    assert await session_adapter.aclear_session("s-async") == 1


def test_long_term_crud_generate_purge_and_validation(
    long_term_adapter: memory_ports.LongTermMemoryPort,
) -> None:
    assert isinstance(long_term_adapter, memory_ports.LongTermMemoryPort)
    created = long_term_adapter.create_memory(
        "Prefers metric units",
        user_id="user_1",
        memory_type="preference",
        metadata={"region": "EU", "priority": 2},
    )
    assert created.user_id == "user_1"
    assert created.memory_type == "preference"
    assert created.metadata["region"] == "EU"
    assert created.metadata["priority"] == 2
    hits = long_term_adapter.retrieve_memories(
        user_id="user_1", query="metric", limit=5
    )
    assert any("metric" in item.fact for item in hits)
    assert any(item.metadata.get("region") == "EU" for item in hits)
    assert len(long_term_adapter.retrieve_memories(user_id="user_1")) == 1
    turns = [
        models.ConversationTurn(
            session_id="s-1", role="user", content="Uses fleet Series-900"
        ),
        models.ConversationTurn(
            session_id="s-1", role="assistant", content="Noted."
        ),
    ]
    gen = long_term_adapter.generate_memories(turns, user_id="user_1")
    assert any("Series-900" in item.fact for item in gen)
    assert long_term_adapter.purge_memories(user_id="user_1") >= 2
    assert long_term_adapter.retrieve_memories(user_id="user_1") == []


def test_long_term_validation_errors(
    long_term_adapter: memory_ports.LongTermMemoryPort,
) -> None:
    for bad_uid in ("", "../admin", 'u" OR 1=1', "a" * 129):
        with pytest.raises(ValueError):
            long_term_adapter.retrieve_memories(user_id=bad_uid)
    for bad_num in (0, -3):
        with pytest.raises(ValueError):
            long_term_adapter.retrieve_memories(user_id="user_1", limit=bad_num)
        with pytest.raises(ValueError):
            long_term_adapter.create_memory(
                "Fact", user_id="user_1", ttl_seconds=bad_num
            )


async def test_long_term_async_parity(
    long_term_adapter: memory_ports.LongTermMemoryPort,
) -> None:
    await long_term_adapter.acreate_memory("Likes diesel", user_id="u-2")
    got = await long_term_adapter.aretrieve_memories(user_id="u-2", query=None)
    assert len(got) == 1
    turns = [
        models.ConversationTurn(
            session_id="s-a", role="user", content="Likes biodiesel"
        )
    ]
    gen = await long_term_adapter.agenerate_memories(turns, user_id="u-2")
    assert any("biodiesel" in item.fact for item in gen)
    assert await long_term_adapter.apurge_memories(user_id="u-2") >= 2


def test_vertex_memory_bank_wire_scope_contract() -> None:
    fake_api = _FakeMemoriesAPI()
    client = SimpleNamespace(agent_engines=SimpleNamespace(memories=fake_api))
    bank = VertexMemoryBank(_ENGINE, client=client)
    turns = [
        models.ConversationTurn(
            session_id="s1", role="user", content="Tractor oil 15W-40"
        )
    ]
    bank.generate_memories(turns, user_id="alice")
    bank.retrieve_memories(user_id="alice", query="oil", limit=3)
    bank.retrieve_memories(user_id="alice", query=None, limit=4)
    assert bank.purge_memories(user_id="alice") == 1

    gen_calls = [kw for op, kw in fake_api.calls if op == "generate"]
    ret_calls = [kw for op, kw in fake_api.calls if op == "retrieve"]
    purge_calls = [kw for op, kw in fake_api.calls if op == "purge"]
    assert gen_calls[0]["scope"] == {"user_id": "alice"}
    assert "type" not in gen_calls[0]["scope"]
    assert ret_calls[0]["scope"] == {"user_id": "alice"}
    assert ret_calls[0]["similarity_search_params"] == {
        "search_query": "oil",
        "top_k": 3,
    }
    assert ret_calls[1]["simple_retrieval_params"] == {"page_size": 4}
    assert purge_calls[0] == {
        "name": _ENGINE,
        "filter": 'scope.user_id="alice"',
        "force": True,
    }
