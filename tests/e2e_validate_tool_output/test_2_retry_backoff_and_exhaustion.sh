#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 2: Retry Mechanism, Exponential Backoff & Exhaustion (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

"${PYTHON_BIN}" - << 'PYEOF'
import asyncio
import time
from pydantic import BaseModel
from pydantic import Field

from agentops_sdk import ToolValidationError
from agentops_sdk import validate_tool_output


class FinancialMetric(BaseModel):
    account_id: str
    revenue: float = Field(..., ge=0.0)
    currency: str = Field("USD", min_length=3, max_length=3)


sync_attempts = 0


@validate_tool_output(schema_model=FinancialMetric, max_retries=4)
def transient_sync_tool():
    global sync_attempts
    sync_attempts += 1
    if sync_attempts < 3:
        return {"account_id": "ACC-100", "revenue": -100.0, "currency": "USD"}
    return {"account_id": "ACC-100", "revenue": 54200.50, "currency": "USD"}


res = transient_sync_tool()
assert sync_attempts == 3 and res["revenue"] == 54200.50

async_attempts = 0


async def run_async_transient():
    global async_attempts

    @validate_tool_output(schema_model=FinancialMetric, max_retries=4)
    async def transient_async_tool():
        global async_attempts
        async_attempts += 1
        await asyncio.sleep(0.01)
        if async_attempts < 2:
            return {"account_id": "ACC-200", "revenue": "non_numeric"}
        return {"account_id": "ACC-200", "revenue": 128900.0, "currency": "USD"}

    out = await transient_async_tool()
    assert async_attempts == 2 and out["revenue"] == 128900.0


asyncio.run(run_async_transient())

timing_attempts = 0


@validate_tool_output(
    schema_model=FinancialMetric,
    max_retries=3,
    retry_delay_seconds=0.08,
    backoff_factor=2.0,
)
def backoff_tool():
    global timing_attempts
    timing_attempts += 1
    if timing_attempts < 3:
        return {"account_id": "ACC-300", "revenue": "invalid"}
    return {"account_id": "ACC-300", "revenue": 1000.0, "currency": "USD"}


t0 = time.perf_counter()
backoff_tool()
elapsed = time.perf_counter() - t0
assert timing_attempts == 3 and elapsed >= 0.22


@validate_tool_output(
    schema_model=FinancialMetric,
    max_retries=4,
    retry_delay_seconds=0.01,
    backoff_factor=1.5,
)
def always_failing_tool():
    return {"account_id": "ACC-FAIL", "revenue": -9999.0}


try:
    always_failing_tool()
    raise AssertionError("always_failing_tool should have raised ToolValidationError")
except ToolValidationError as err:
    assert err.attempts == 4
    assert err.tool_name == "always_failing_tool"
    assert isinstance(err.last_error, str) and len(err.last_error) > 0
    assert "always_failing_tool" in str(err)

print("[PASS] Test 2: Retry, exponential backoff & exhaustion verified.")
PYEOF
