# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Tutorial 12: Cloud SQL session memory + Vertex AI Memory Bank."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared import settings
from _shared import telemetry

cfg = settings.load("12_memory")
tel = telemetry.start(cfg)

from agentops_sdk import CloudSQLSessionMemory
from agentops_sdk import CompositeMemory
from agentops_sdk import VertexMemoryBank

USER = "dealer-4711"
SESSION = f"session-{cfg.run_id}"


def _build_memory_bank() -> VertexMemoryBank:
    name = settings.require("MEMORY_BANK_NAME")
    location = os.getenv("MEMORY_BANK_LOCATION", cfg.location)
    bank = VertexMemoryBank(
        reasoning_engine_name=name,
        project_id=cfg.project,
        location=location,
    )
    print(f"\nA) Memory Bank: {name.rsplit('/', 1)[-1]} in {location}")
    return bank


def _build_session_store() -> CloudSQLSessionMemory:
    url = settings.require("DATABASE_URL")
    if not url.startswith("postgresql"):
        sys.exit(
            "DATABASE_URL must point to Cloud SQL PostgreSQL "
            "(refusing local sqlite)."
        )
    mem = CloudSQLSessionMemory(
        connection_url=url, table_name="agentops_tutorial_sessions"
    )
    print(f"\nB) Cloud SQL session store via {url.split('@')[-1]}")
    return mem


def _exercise_memory_bank(bank: VertexMemoryBank) -> None:
    bank.purge_memories(user_id=USER)
    bank.create_memory(
        "Sells Series-700 and Series-800 equipment in Region-A", user_id=USER
    )
    bank.create_memory(
        "Prefers answers in German with part numbers", user_id=USER
    )
    stored = []
    for _ in range(6):
        stored = bank.retrieve_memories(user_id=USER, limit=5)
        if len(stored) >= 2:
            break
        time.sleep(5)
    print(f"   stored for {USER}: {[m.fact for m in stored]}")
    if len(stored) < 2:
        sys.exit("Memory Bank did not return stored facts after 30 s.")

    hits = bank.retrieve_memories(
        user_id=USER,
        query="which equipment lines does the dealer sell?",
        limit=1,
    )
    print(f"   semantic retrieval -> {[h.fact for h in hits]}")


def main() -> None:
    """Exercise Memory Bank retrieval, Cloud SQL turns, and CompositeMemory."""
    bank = _build_memory_bank()
    _exercise_memory_bank(bank)

    session = _build_session_store()
    session.add_message(
        SESSION, "user", "Do you have oil filters for a Series-700?"
    )
    session.add_message(
        SESSION,
        "assistant",
        "Yes, 7 units of PART-1001 in Plant-EU-01.",
    )
    history = session.get_messages(SESSION)
    print(f"   {len(history)} turns stored in Postgres for {SESSION}")

    composite = CompositeMemory(session_memory=session, long_term_memory=bank)
    context = composite.get_context(
        SESSION,
        user_id=USER,
        query="which equipment lines does the dealer sell?",
        strict=True,
    )
    print(f"\nC) CompositeMemory.get_context keys: {sorted(context)}")
    tel.flush()


if __name__ == "__main__":
    main()
