#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 3: Dialect case-folding, quoted identifiers, and TVF/PIVOT/column-list rejection

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MODE="${MODE:-hermetic}"
export MODE

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

"${PYTHON_BIN}" - << 'PYEOF'
import os
from agentops_sdk.domain.exceptions import SQLGovernanceError
from agentops_sdk.sql import SQLASTValidator, apply_row_level_policy

mode = os.environ.get("MODE", "hermetic")

# 1. Postgres dialect case-folding vs quoted CTE identifiers
pg_val = SQLASTValidator(
    dialect="postgres",
    allowed_tables=["enterprise_parts.inventory"],
)
unquoted_cte = pg_val.validate(
    "WITH MyCTE AS (SELECT part_number FROM enterprise_parts.inventory) "
    "SELECT part_number FROM mycte"
)
assert unquoted_cte.is_safe, f"Unquoted CTE should fold in Postgres: {unquoted_cte.violation_reason}"

quoted_cte_mismatch = pg_val.validate(
    'WITH "MyCTE" AS (SELECT part_number FROM enterprise_parts.inventory) '
    "SELECT part_number FROM mycte"
)
assert not quoted_cte_mismatch.is_safe, (
    "Quoted mixed-case CTE in Postgres should not match unquoted lowercase ref"
)

# 2. BigQuery backtick-quoted identifiers
bq_val = SQLASTValidator(
    dialect="bigquery",
    allowed_tables=["enterprise_parts.inventory"],
)
assert bq_val.validate(
    "SELECT part_number FROM `enterprise_parts`.`inventory`"
).is_safe

# 3. TVF / PIVOT / column-list rejection in RLS engine
claims = {"tenant_id": "tenant-acme"}
for bad_rls, rls_dialect in (
    (
        "SELECT * FROM enterprise_parts.inventory PIVOT(SUM(units_available) FOR region IN ('US', 'EU'))",
        "bigquery",
    ),
    (
        "SELECT * FROM enterprise_parts.inventory AS inv(a, b)",
        "postgres",
    ),
    (
        "SELECT * FROM my_tvf(1)",
        "bigquery",
    ),
):
    try:
        apply_row_level_policy(bad_rls, claims, dialect=rls_dialect)
        raise AssertionError(f"Expected SQLGovernanceError for: {bad_rls}")
    except SQLGovernanceError:
        pass

print(
    f"[PASS] Dialect case-folding, quoted identifiers, and TVF/PIVOT/column-list rejection verified ({mode})."
)
PYEOF
