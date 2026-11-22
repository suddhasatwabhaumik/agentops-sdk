#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 3: CloudSQLSessionMemory isolation, keyset pagination, and async thread safety

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
import os
import tempfile
from agentops_sdk.memory import CloudSQLSessionMemory

mode = "${MODE}"
if mode == "live":
    db_url = os.environ["CLOUDSQL_URL"]
    tmp_path = None
else:
    fd, tmp_path = tempfile.mkstemp(prefix="agentops_sess_", suffix=".db")
    os.close(fd)
    db_url = f"sqlite:///{tmp_path}"

try:
    mem = CloudSQLSessionMemory(db_url, table_name="e2e_session_turns")
    s1 = "tenant-acme-sess-1"
    s2 = "tenant-acme-sess-2"

    for i in range(5):
        mem.add_message(s1, "user", f"Turn {i} for UNIT-001", metadata={"idx": i})
    mem.add_message(s2, "user", "Isolated turn for Series-700")

    # Session isolation
    s1_turns = mem.get_messages(s1, limit=10)
    s2_turns = mem.get_messages(s2, limit=10)
    assert len(s1_turns) == 5
    assert len(s2_turns) == 1
    assert s2_turns[0].content == "Isolated turn for Series-700"

    # Keyset pagination (after_id / before_id)
    page1 = mem.get_messages(s1, limit=2)
    assert len(page1) == 2
    page2 = mem.get_messages(s1, limit=2, after_id=page1[-1].id)
    assert len(page2) == 2
    assert page2[0].id > page1[-1].id
    prev = mem.get_messages(s1, limit=10, before_id=page2[0].id)
    assert [t.id for t in prev] == [t.id for t in page1]

    # Concurrent async thread safety
    async def run_concurrent():
        await asyncio.gather(
            *(
                mem.aadd_message("tenant-acme-concurrent", "user", f"msg-{k}")
                for k in range(12)
            )
        )
        rows = await mem.aget_messages("tenant-acme-concurrent", limit=50)
        assert len(rows) == 12

    asyncio.run(run_concurrent())
    assert mem.clear_session(s1) == 5
    assert mem.get_messages(s1, limit=10) == []
    print(f"[PASS] CloudSQLSessionMemory isolation, keyset pagination, and async concurrency verified ({mode}).")
finally:
    if tmp_path and os.path.exists(tmp_path):
        os.remove(tmp_path)
PYEOF
