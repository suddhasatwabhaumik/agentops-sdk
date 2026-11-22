#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 4: @validate_sql_query_decorator + apply_row_level_policy sync & async

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
from agentops_sdk.domain.exceptions import SQLGovernanceError
from agentops_sdk.sql import apply_row_level_policy, validate_sql_query_decorator

@validate_sql_query_decorator(
    dialect="bigquery",
    allowed_tables=["enterprise_parts.inventory"],
    sql_params=["sql"],
)
def run_sync_query(sql: str) -> str:
    return sql

@validate_sql_query_decorator(
    dialect="bigquery",
    allowed_tables=["enterprise_parts.inventory"],
    sql_params=["sql"],
)
async def run_async_query(sql: str) -> str:
    await asyncio.sleep(0)
    return sql

raw = "SELECT part_number, units_available FROM enterprise_parts.inventory WHERE part_number = 'FLT-2750'"
secured = apply_row_level_policy(raw, {"tenant_id": "tenant-acme"})
assert "tenant-acme" in secured

assert "tenant-acme" in run_sync_query(sql=secured)
assert "tenant-acme" in asyncio.run(run_async_query(sql=secured))

# Unsafe table rejected by decorator on both sync and async
bad_sql = "SELECT * FROM finance.credit_limits"
for fn_call in (
    lambda: run_sync_query(sql=bad_sql),
    lambda: asyncio.run(run_async_query(sql=bad_sql)),
):
    try:
        fn_call()
        raise AssertionError("Expected SQLGovernanceError for unauthorized table")
    except SQLGovernanceError:
        pass

print("[PASS] @validate_sql_query_decorator + apply_row_level_policy sync & async verified (${MODE}).")
PYEOF
