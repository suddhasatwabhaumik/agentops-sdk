#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 1: VertexMemoryBank CRUD, canonical user_id scope, memory_type, and purge

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MODE="${MODE:-hermetic}"
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-example-tenant-01}}"
LOCATION="${2:-${GOOGLE_CLOUD_LOCATION:-europe-west4}}"
REASONING_ENGINE="${AGENTOPS_REASONING_ENGINE:-1234567890}"

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

"${PYTHON_BIN}" - << PYEOF
import os
import types
from agentops_sdk.memory import VertexMemoryBank, validate_user_id

mode = "${MODE}"

class FakeMemoriesService:
    def __init__(self):
        self.store = []
        self.seq = 0

    def create(self, *, name, scope, fact, config=None):
        self.seq += 1
        uid = scope["user_id"]
        meta = (config or {}).get("metadata", {})
        rec = {
            "name": f"{name}/memories/mem-{self.seq}",
            "fact": fact,
            "metadata": meta,
            "scope": dict(scope),
        }
        self.store.append(rec)
        return rec

    def retrieve(self, *, name, scope, simple_retrieval_params=None, similarity_search_params=None):
        uid = scope["user_id"]
        rows = [r for r in self.store if r["scope"].get("user_id") == uid]
        lim = (simple_retrieval_params or {}).get("page_size") or (similarity_search_params or {}).get("top_k") or 5
        return rows[:lim]

    def purge(self, *, name, filter, force=True):
        prefix = 'scope.user_id="'
        uid = filter[len(prefix):-1] if filter.startswith(prefix) else ""
        before = len(self.store)
        self.store = [r for r in self.store if r["scope"].get("user_id") != uid]
        return {"purge_count": before - len(self.store)}

fake_client = None
if mode == "hermetic":
    fake_client = types.SimpleNamespace(
        agent_engines=types.SimpleNamespace(memories=FakeMemoriesService())
    )

bank = VertexMemoryBank(
    "${REASONING_ENGINE}",
    project_id="${PROJECT_ID}",
    location="${LOCATION}",
    client=fake_client,
)

# Canonical user_id validation
assert validate_user_id("tenant-acme:user-01") == "tenant-acme:user-01"
try:
    validate_user_id("bad\nuser")
    raise AssertionError("Expected ValueError for invalid user_id")
except ValueError:
    pass

uid = "tenant-acme:unit-001"
m_fact = bank.create_memory(
    "Series-700 uses hydraulic filter FLT-2750.",
    user_id=uid,
    memory_type="fact",
    metadata={"unit": "UNIT-001"},
)
m_pref = bank.create_memory(
    "Prefer metric units in all technical responses.",
    user_id=uid,
    memory_type="preference",
)
assert m_fact.fact.startswith("Series-700")
assert m_pref.memory_type == "preference"

# Scope isolation: another user sees nothing
other_items = bank.retrieve_memories(user_id="tenant-acme:other-user", limit=10)
assert other_items == [], f"Expected empty scope, got {other_items}"

# memory_type filtering
prefs = bank.retrieve_memories(user_id=uid, memory_type="preference", limit=10)
assert len(prefs) == 1 and prefs[0].memory_type == "preference"

all_items = bank.retrieve_memories(user_id=uid, memory_type="all", limit=10)
assert len(all_items) >= 2

purged = bank.purge_memories(user_id=uid)
assert purged >= 2, f"Expected >=2 purged, got {purged}"
assert bank.retrieve_memories(user_id=uid, limit=10) == []
print(f"[PASS] VertexMemoryBank CRUD, scopes, filtering, and purge verified ({mode}).")
PYEOF
