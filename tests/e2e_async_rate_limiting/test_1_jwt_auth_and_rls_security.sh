#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 1: Cryptographic JWT Auth, Backdoor Rejection & Tenant RLS Security (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

echo "[TEST 1 INFO] Running Cryptographic JWT, Auth Guard & Tenant RLS Suite..."

"${PYTHON_BIN}" - << 'PYEOF'
import secrets

from agentops_sdk import JWTAuthEngine
from agentops_sdk import apply_row_level_policy
from agentops_sdk.domain.exceptions import AuthenticationError
from agentops_sdk.domain.exceptions import SQLGovernanceError
from mcp_servers.src import auth_guard

engine = JWTAuthEngine(secret_key=secrets.token_urlsafe(48))

print("[1.1] Verifying mock token backdoor is rejected...")
try:
    auth_guard.authenticate_caller(
        headers={"Authorization": "Bearer mock-token-attacker"},
        jwt_engine=engine,
    )
    raise AssertionError("Mock token backdoor was accepted!")
except AuthenticationError:
    print("  => Mock token rejected cleanly (AuthenticationError).")

print("[1.2] Verifying forged unsigned JWT is rejected...")
forged = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJhdHRhY2tlciIsInRlbmFudF9pZCI6InZpY3RpbSJ9.fake_sig"
)
try:
    auth_guard.authenticate_caller(token=forged, jwt_engine=engine)
    raise AssertionError("Forged token was accepted!")
except AuthenticationError:
    print("  => Forged unsigned JWT rejected cleanly.")

print("[1.3] Verifying signed JWT authorization + SQL AST + RLS injection...")
tenant_id = "tenant-secure-corp"
valid_token = engine.create_access_token(
    {"sub": "authorized-user", "tenant_id": tenant_id, "role": "analyst"}
)
claims, out_headers = auth_guard.authenticate_caller(
    token=valid_token, jwt_engine=engine
)
assert claims is not None and claims["tenant_id"] == tenant_id
assert out_headers["Authorization"] == f"Bearer {valid_token}"
rewritten, _ = auth_guard.authorize_query(
    "SELECT order_id, total FROM orders WHERE total > 100",
    token=valid_token,
    allowed_tables=["orders"],
    jwt_engine=engine,
)
assert tenant_id in rewritten
print(f"  => Authorized + RLS rewritten SQL: {rewritten}")

print("[1.4] Verifying unauthorized table fails AST guard...")
try:
    auth_guard.authorize_query(
        "SELECT secret_col FROM payroll.salaries",
        token=valid_token,
        allowed_tables=["orders"],
        jwt_engine=engine,
    )
    raise AssertionError("Disallowed table escaped AST guard!")
except SQLGovernanceError:
    print("  => Unauthorized table blocked by SQL AST guard.")

print("[1.5] Verifying explicit admin bypass vs tenant feature-store lock...")
admin_sql = apply_row_level_policy(
    "SELECT order_id FROM orders",
    {"role": "admin"},
    dialect="bigquery",
)
assert "WHERE" not in admin_sql.upper()
try:
    auth_guard.authorize_feature_store_tenant(
        "dealer", ["other-tenant:sku-1"], claims
    )
    raise AssertionError("Expected AuthenticationError for cross-tenant read")
except AuthenticationError:
    print("  => Admin bypass and feature-store tenant check verified.")
PYEOF

echo "[TEST 1 PASS] JWT authentication, backdoor removal, and RLS verified."
