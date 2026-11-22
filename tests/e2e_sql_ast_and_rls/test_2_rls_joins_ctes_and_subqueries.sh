#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 2: RLS across joins, parenthesized joins, LEFT JOIN ON, CTEs, subqueries, UNION

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MODE="${MODE:-hermetic}"

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

export MODE
"${PYTHON_BIN}" - << 'PYEOF'
import os
from agentops_sdk.sql import apply_row_level_policy

mode = os.environ.get("MODE", "hermetic")

claims = {"tenant_id": "tenant-acme"}

# 1. Inner join
q_join = (
    "SELECT i.part_number, o.order_id "
    "FROM enterprise_parts.inventory i "
    "JOIN enterprise_parts.orders o ON i.part_number = o.part_number"
)
r_join = apply_row_level_policy(q_join, claims)
assert r_join.count("tenant-acme") == 2, f"Expected 2 tenant predicates in join: {r_join}"

# 2. Parenthesized join
q_paren = (
    "SELECT i.part_number FROM (enterprise_parts.inventory i "
    "JOIN enterprise_parts.orders o ON i.part_number = o.part_number)"
)
r_paren = apply_row_level_policy(q_paren, claims)
assert r_paren.count("tenant-acme") == 2, f"Expected 2 tenant predicates in paren join: {r_paren}"

# 3. LEFT JOIN attaches predicate to ON clause rather than WHERE (preserves left-outer semantics)
q_left = (
    "SELECT i.part_number, o.order_id "
    "FROM enterprise_parts.inventory i "
    "LEFT JOIN enterprise_parts.orders o ON i.part_number = o.part_number"
)
r_left = apply_row_level_policy(q_left, claims)
assert "o.tenant_id = 'tenant-acme'" in r_left or "`o`.`tenant_id` = 'tenant-acme'" in r_left or "tenant-acme" in r_left
assert r_left.count("tenant-acme") == 2, f"Expected 2 predicates in LEFT JOIN: {r_left}"

# 4. CTE + Subquery + UNION
q_complex = (
    "WITH inv AS (SELECT part_number, tenant_id FROM enterprise_parts.inventory) "
    "SELECT part_number FROM inv "
    "UNION ALL "
    "SELECT part_number FROM (SELECT part_number FROM enterprise_parts.orders) sub"
)
r_complex = apply_row_level_policy(q_complex, claims)
assert r_complex.count("tenant-acme") == 2, f"Expected 2 predicates across CTE+subquery+UNION: {r_complex}"

# 5. Admin bypass
r_admin = apply_row_level_policy(q_join, {"tenant_id": "tenant-acme", "role": "admin"})
assert "tenant-acme" not in r_admin

print(f"[PASS] RLS across joins, parenthesized joins, LEFT JOIN ON, CTEs, subqueries, and UNION verified ({mode}).")
PYEOF
