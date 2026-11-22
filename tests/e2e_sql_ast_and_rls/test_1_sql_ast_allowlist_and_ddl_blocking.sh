#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 1: SQLASTValidator allowlist matching, DDL/DML blocking, and ML.PREDICT

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
from agentops_sdk.sql import SQLASTValidator

validator = SQLASTValidator(
    dialect="bigquery",
    allowed_tables=["enterprise_parts.inventory"],
)

# 1. Valid allowlisted SELECT
ok = validator.validate(
    "SELECT part_number, units_available FROM enterprise_parts.inventory WHERE part_number = 'FLT-2750'"
)
assert ok.is_safe, f"Expected safe query, got {ok.violation_reason}"
assert "enterprise_parts.inventory" in ok.referenced_tables

# 2. DDL / DML / multi-statement blocking
for bad_sql in (
    "DROP TABLE enterprise_parts.inventory",
    "DELETE FROM enterprise_parts.inventory WHERE 1 = 1",
    "INSERT INTO enterprise_parts.inventory (part_number) VALUES ('X')",
    "SELECT 1; SELECT 2",
    "SELECT * FROM finance.credit_limits",
    "SELECT * FROM INFORMATION_SCHEMA.TABLES",
):
    res = validator.validate(bad_sql)
    assert not res.is_safe, f"Expected rejection for: {bad_sql}"

# 3. Sole-FROM ML.PREDICT allowed vs. blocked TVFs
ml_ok = validator.validate(
    "SELECT * FROM ML.PREDICT(MODEL enterprise_parts.demand_model, "
    "(SELECT part_number FROM enterprise_parts.inventory))"
)
assert ml_ok.is_safe, f"Expected sole-FROM ML.PREDICT to be allowed, got {ml_ok.violation_reason}"

tvf_bad = validator.validate(
    "SELECT * FROM EXTERNAL_QUERY('conn-id', 'SELECT * FROM enterprise_parts.inventory')"
)
assert not tvf_bad.is_safe, "Expected EXTERNAL_QUERY TVF to be blocked"

print("[PASS] SQLASTValidator allowlist, DDL/DML blocking, and ML.PREDICT verified (${MODE}).")
PYEOF
