# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for agentops_sdk.memory adapters and CompositeMemory."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from agentops_sdk.domain import exceptions
from agentops_sdk.domain import models
from agentops_sdk.memory import CloudSQLSessionMemory
from agentops_sdk.memory import CompositeMemory
from agentops_sdk.memory import InMemoryLongTermMemory
from agentops_sdk.memory import InMemorySessionMemory
from agentops_sdk.memory import VertexMemoryBank
from agentops_sdk.memory import cloud_sql


def test_in_memory_long_term_ttl_and_scoring() -> None:
    ltm = InMemoryLongTermMemory()
    ltm.create_memory("Prefers Series-900 equipment", user_id="u1")
    ltm.create_memory("Temporary note", user_id="u1", ttl_seconds=1)
    assert len(ltm.retrieve_memories(user_id="u1")) == 2
    hits = ltm.retrieve_memories(user_id="u1", query="Series-900 equipment")
    assert hits[0].fact == "Prefers Series-900 equipment"
    assert hits[0].score is not None and hits[0].score > 0.0

    ltm._records["u1"][1] = (ltm._records["u1"][1][0], time.monotonic() - 1.0)
    assert len(ltm.retrieve_memories(user_id="u1")) == 1
    with pytest.raises(ValueError):
        ltm.create_memory("bad", user_id="u1", ttl_seconds=0)


async def test_cloud_sql_session_memory_concurrency_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(exceptions.ConfigurationError):
        CloudSQLSessionMemory("")
    with pytest.raises(ValueError):
        CloudSQLSessionMemory("sqlite:///:memory:", table_name="bad;table")
    mem = CloudSQLSessionMemory("sqlite:///:memory:")
    t1 = mem.add_message("s1", "user", "one")
    t2 = mem.add_message("s1", "assistant", "two")
    assert [t.content for t in mem.get_messages("s1", before_id=t2.id)] == [
        "one"
    ]
    assert t1.id is not None

    created = await asyncio.gather(
        *(mem.aadd_message("s-conc", "user", f"m-{i}") for i in range(20))
    )
    assert len(created) == 20
    fetched = await mem.aget_messages("s-conc", limit=30)
    assert len(fetched) == 20
    assert {m.content for m in fetched} == {f"m-{i}" for i in range(20)}
    mem.close()

    monkeypatch.setattr(cloud_sql, "sqlalchemy", None)
    with pytest.raises(exceptions.ConfigurationError):
        CloudSQLSessionMemory("sqlite:///:memory:")


def _make_aio_vertex_client() -> SimpleNamespace:
    async def _acreate(**kw: Any) -> SimpleNamespace:
        return SimpleNamespace(name="mem-1", fact=kw["fact"], metadata={})

    async def _aretrieve(**_kw: Any) -> Any:
        async def _gen() -> Any:
            yield SimpleNamespace(name="mem-1", fact="async fact", score=0.9)

        return _gen()

    aio_mem = SimpleNamespace(create=_acreate, retrieve=_aretrieve)
    sync_mem = SimpleNamespace(
        purge=lambda **_kw: (_ for _ in ()).throw(RuntimeError("RPC 500"))
    )
    return SimpleNamespace(
        agent_engines=SimpleNamespace(memories=sync_mem),
        aio=SimpleNamespace(agent_engines=SimpleNamespace(memories=aio_mem)),
    )


async def test_vertex_memory_bank_aio_and_errors() -> None:
    with pytest.raises(ValueError):
        VertexMemoryBank(
            "bad/path",
            project_id="test-proj",
            location="europe-west4",
            client=SimpleNamespace(),
        )
    bank = VertexMemoryBank(
        "engine-01",
        project_id="test-proj",
        location="europe-west4",
        client=_make_aio_vertex_client(),
    )
    assert bank.engine_name.endswith("/reasoningEngines/engine-01")
    item = await bank.acreate_memory(
        "async fact", user_id="u1", metadata={"count": 2}, ttl_seconds=60
    )
    assert item.fact == "async fact"
    got = await bank.aretrieve_memories(user_id="u1", query="async")
    assert len(got) == 1 and got[0].score == 0.9
    with pytest.raises(exceptions.MemoryBankError):
        bank.purge_memories(user_id="u1")


async def test_composite_memory_context_and_consolidation() -> None:
    bad: Any = "bad"
    with pytest.raises(TypeError):
        CompositeMemory(bad, InMemoryLongTermMemory())
    with pytest.raises(TypeError):
        CompositeMemory(InMemorySessionMemory(), bad)

    session = InMemorySessionMemory()
    comp = CompositeMemory(session, InMemoryLongTermMemory())
    comp.add_message("s1", "user", "Needs 15W-40 oil")
    comp.add_message("s1", "assistant", "Stored.")
    items = comp.consolidate_session("s1", "u1", clear_after=True)
    assert len(items) == 1 and comp.get_messages("s1") == []

    await comp.aadd_message("s2", "user", "Uses hydraulic filter")
    await comp.aconsolidate_session("s2", "u1", clear_after=False)
    ctx = await comp.aget_context("s2", "u1", query="oil")
    assert len(ctx["short_term_history"]) == 1
    assert len(ctx["long_term_memories"]) == 1


async def test_composite_memory_degraded_recall() -> None:
    class _FailingLTM(InMemoryLongTermMemory):
        def retrieve_memories(self, **_kw: Any) -> list[models.MemoryItem]:
            raise exceptions.MemoryBankError("backend down")

        async def aretrieve_memories(
            self, **_kw: Any
        ) -> list[models.MemoryItem]:
            raise exceptions.MemoryBankError("backend down")

    degraded = CompositeMemory(InMemorySessionMemory(), _FailingLTM())
    assert degraded.get_context("s2", "u1", strict=False)["memories"] == []
    with pytest.raises(exceptions.MemoryBankError):
        degraded.get_context("s2", "u1", strict=True)
    assert (await degraded.aget_context("s2", "u1", strict=False))[
        "memories"
    ] == []


async def test_memory_type_filtering_and_bounded_overfetch() -> None:
    ltm = InMemoryLongTermMemory()
    comp = CompositeMemory(InMemorySessionMemory(), ltm)
    comp.create_memory(
        "Prefers Series-900", user_id="u1", memory_type="preference"
    )
    comp.create_memory("Operates UNIT-001", user_id="u1", memory_type="fact")

    assert [
        m.fact
        for m in comp.retrieve_memories(user_id="u1", memory_type="preference")
    ] == ["Prefers Series-900"]
    assert len(comp.retrieve_memories(user_id="u1", memory_type="all")) == 2
    assert len(comp.retrieve_memories(user_id="u1", memory_type=None)) == 2
    assert [
        m.fact
        for m in await comp.aretrieve_memories(
            user_id="u1", memory_type="preference"
        )
    ] == ["Prefers Series-900"]

    seen_kw: list[dict[str, Any]] = []

    async def _aretrieve(**kw: Any) -> Any:
        seen_kw.append(kw)

        async def _gen() -> Any:
            yield SimpleNamespace(
                name="m1",
                fact="Operates UNIT-001",
                metadata={"memory_type": "fact"},
                score=0.8,
            )
            yield SimpleNamespace(
                name="m2",
                fact="Prefers Series-900",
                metadata={"memory_type": "preference"},
                score=0.9,
            )

        return _gen()

    client = SimpleNamespace(
        agent_engines=SimpleNamespace(memories=SimpleNamespace()),
        aio=SimpleNamespace(
            agent_engines=SimpleNamespace(
                memories=SimpleNamespace(retrieve=_aretrieve)
            )
        ),
    )
    bank = VertexMemoryBank(
        "engine-01",
        project_id="test-proj",
        location="europe-west4",
        client=client,
    )
    got = await bank.aretrieve_memories(
        user_id="u1", limit=3, memory_type="preference"
    )
    assert [m.fact for m in got] == ["Prefers Series-900"]
    assert seen_kw[0]["simple_retrieval_params"]["page_size"] == 20
