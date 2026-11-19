#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 3: Assertion Callbacks, Async Await Integrity & Contract Guards (v1)

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
from pydantic import BaseModel
from pydantic import Field

from agentops_sdk import ToolValidationError
from agentops_sdk import validate_tool_output


def check_no_pii(result):
    return "ssn" not in str(result).lower()


@validate_tool_output(assertion_callback=check_no_pii)
def safe_sync_tool():
    return {"user_id": "U-123", "email": "user@example.com"}


assert safe_sync_tool()["user_id"] == "U-123"


@validate_tool_output(assertion_callback=check_no_pii, max_retries=2)
def leaking_sync_tool():
    return {"user_id": "U-999", "ssn": "000-00-0000"}


try:
    leaking_sync_tool()
    raise AssertionError("leaking_sync_tool should have failed")
except ToolValidationError as err:
    assert err.attempts == 2
    assert "assertion" in err.last_error.lower()


async def run_async_callbacks():
    async def async_no_forbidden(output):
        await asyncio.sleep(0.01)
        return "restricted" not in output

    @validate_tool_output(assertion_callback=async_no_forbidden)
    async def safe_async_tool():
        return {"status": "ok", "records_processed": 100}

    assert (await safe_async_tool())["status"] == "ok"

    attempts = 0

    @validate_tool_output(assertion_callback=async_no_forbidden, max_retries=3)
    async def leaking_async_tool():
        nonlocal attempts
        attempts += 1
        return {"status": "error", "restricted": "present"}

    try:
        await leaking_async_tool()
        raise AssertionError("leaking_async_tool should have failed")
    except ToolValidationError as err:
        assert attempts == 3 and err.attempts == 3
        assert "assertion" in err.last_error.lower()

    async def async_validate_rows(output):
        await asyncio.sleep(0.01)
        if output.get("rows_count", 0) <= 0:
            raise ValueError("Dataset empty: rows_count must be > 0")
        return True

    @validate_tool_output(assertion_callback=async_validate_rows, max_retries=2)
    async def empty_dataset_tool():
        return {"dataset": "empty_table", "rows_count": 0}

    try:
        await empty_dataset_tool()
        raise AssertionError("empty_dataset_tool should have failed")
    except ToolValidationError as err:
        assert err.attempts == 2
        assert "Dataset empty" in err.last_error


asyncio.run(run_async_callbacks())


async def dummy_async_cb(_output):
    return True


@validate_tool_output(assertion_callback=dummy_async_cb, max_retries=2)
def sync_tool_with_async_cb():
    return {"sample": "data"}


try:
    sync_tool_with_async_cb()
    raise AssertionError("sync tool with async callback must fail")
except ToolValidationError as err:
    assert "Async assertion_callback cannot be used with a sync tool." in err.last_error


@validate_tool_output(max_retries=0)
def zero_retries_tool():
    return {"success": True}


assert zero_retries_tool()["success"] is True


class ReportSchema(BaseModel):
    report_name: str
    total_records: int = Field(..., gt=0)


@validate_tool_output(
    schema_model=ReportSchema,
    assertion_callback=lambda out: out["total_records"] < 1000000,
)
def combined_tool():
    return {"report_name": "daily_audit", "total_records": 500}


assert combined_tool()["report_name"] == "daily_audit"
print("[PASS] Test 3: Assertion callbacks and contract guards verified.")
PYEOF
