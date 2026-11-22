#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 4: CompositeMemory unified session + long-term memory & consolidation

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MODE="${MODE:-hermetic}"

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

"${PYTHON_BIN}" - << PYEOF
import asyncio
from agentops_sdk.memory import (
    CloudSQLSessionMemory,
    CompositeMemory,
    InMemoryLongTermMemory,
)

mode = "${MODE}"

with CompositeMemory(
    CloudSQLSessionMemory("sqlite:///:memory:"),
    InMemoryLongTermMemory(),
) as memory:
    uid = "tenant-acme:advisor-01"
    sid = "sess-unit-001"

    memory.create_memory(
        "Prefer metric units for Series-700 specs.",
        user_id=uid,
        memory_type="preference",
    )
    memory.add_message(sid, "user", "Check hydraulic filter FLT-2750 for UNIT-001.")
    memory.add_message(sid, "assistant", "42 units of FLT-2750 are available.")

    ctx = memory.get_context(sid, uid, query="Series-700", turn_limit=10, memory_limit=5)
    assert len(ctx["short_term_history"]) == 2
    assert len(ctx["long_term_memories"]) == 1
    assert ctx["memories"][0].memory_type == "preference"

    consolidated = memory.consolidate_session(sid, uid, clear_after=True)
    assert len(consolidated) >= 1
    assert memory.get_messages(sid, limit=10) == []

    async def check_async():
        await memory.aadd_message(sid, "user", "Schedule restock for Series-700.")
        actx = await memory.aget_context(sid, uid, query="FLT-2750", turn_limit=5, memory_limit=5)
        assert len(actx["turns"]) == 1
        assert len(actx["memories"]) >= 1

    asyncio.run(check_async())
    print(f"[PASS] CompositeMemory unified context & consolidation verified ({mode}).")
PYEOF
