#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 2: Semantic similarity search, top_k, and memory_type bounded over-fetch

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
import re
import types
from agentops_sdk.memory import VertexMemoryBank

mode = "${MODE}"

class FakeSimilarityMemories:
    def __init__(self):
        self.store = []
        self.calls = []
        self.seq = 0

    def create(self, *, name, scope, fact, config=None):
        self.seq += 1
        rec = {
            "name": f"{name}/memories/mem-{self.seq}",
            "fact": fact,
            "metadata": (config or {}).get("metadata", {}),
            "scope": dict(scope),
        }
        self.store.append(rec)
        return rec

    def retrieve(self, *, name, scope, similarity_search_params=None, simple_retrieval_params=None):
        self.calls.append(
            {
                "similarity_search_params": similarity_search_params,
                "simple_retrieval_params": simple_retrieval_params,
            }
        )
        uid = scope["user_id"]
        rows = [r for r in self.store if r["scope"].get("user_id") == uid]
        if similarity_search_params:
            q_tokens = set(re.findall(r"[a-z0-9]+", similarity_search_params["search_query"].lower()))
            top_k = similarity_search_params["top_k"]
            scored = []
            for r in rows:
                f_tokens = set(re.findall(r"[a-z0-9]+", r["fact"].lower()))
                score = len(q_tokens & f_tokens) / max(1, len(q_tokens))
                scored.append({**r, "score": round(score, 4)})
            scored.sort(key=lambda x: -x["score"])
            return scored[:top_k]
        return rows

    def purge(self, *, name, filter, force=True):
        self.store.clear()
        return {"purge_count": 0}

fake_svc = FakeSimilarityMemories()
fake_client = (
    types.SimpleNamespace(agent_engines=types.SimpleNamespace(memories=fake_svc))
    if mode == "hermetic"
    else None
)

bank = VertexMemoryBank(
    "${REASONING_ENGINE}",
    project_id="${PROJECT_ID}",
    location="${LOCATION}",
    client=fake_client,
)

uid = "tenant-acme:search-user"
for i in range(6):
    bank.create_memory(f"General maintenance log entry {i} for UNIT-001.", user_id=uid, memory_type="fact")
bank.create_memory("Prefer concise bulleted summaries for Series-700.", user_id=uid, memory_type="preference")
bank.create_memory("Prefer metric torque values for Series-700.", user_id=uid, memory_type="preference")

# Filtered search should over-fetch (min(max(limit * 4, 20), 100)) so preferences are not starved
prefs = bank.retrieve_memories(
    user_id=uid,
    query="Series-700 torque",
    limit=2,
    memory_type="preference",
)
assert len(prefs) == 2
assert all(p.memory_type == "preference" for p in prefs)
if mode == "hermetic":
    last_params = fake_svc.calls[-1]["similarity_search_params"]
    assert last_params["top_k"] == 20, f"Expected bounded over-fetch top_k=20, got {last_params}"

# Unfiltered search uses exact limit as top_k
top1 = bank.retrieve_memories(user_id=uid, query="Series-700 torque", limit=1)
assert len(top1) == 1
if mode == "hermetic":
    assert fake_svc.calls[-1]["similarity_search_params"]["top_k"] == 1

bank.purge_memories(user_id=uid)
print(f"[PASS] Semantic similarity search, top_k, and bounded over-fetch verified ({mode}).")
PYEOF
