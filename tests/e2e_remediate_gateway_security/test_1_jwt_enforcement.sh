#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

echo "========================================================"
echo "E2E Test 1: Cryptographic JWT Enforcement & Fail-Closed Secrets (v1)"
echo "========================================================"

"${PYTHON_BIN}" - << 'PYEOF'
import secrets

from agentops_sdk import InMemorySecretStore
from agentops_sdk import JWTAuthEngine
from agentops_sdk import fetch_secret
from agentops_sdk.domain.exceptions import AuthenticationError
from agentops_sdk.domain.exceptions import ConfigurationError
from agentops_sdk.domain.exceptions import SecretAccessError
from mcp_servers.src import auth_guard

# 1. Fail-closed on missing signing key in production
import os
prev_env = os.environ.get("ENVIRONMENT")
os.environ["ENVIRONMENT"] = "production"
try:
    try:
        JWTAuthEngine()
        raise AssertionError("JWTAuthEngine must fail closed in production without key!")
    except (ConfigurationError, AuthenticationError):
        pass
finally:
    if prev_env is None:
        os.environ.pop("ENVIRONMENT", None)
    else:
        os.environ["ENVIRONMENT"] = prev_env

# 2. Signed access & refresh tokens + type confusion rejection
engine = JWTAuthEngine(secret_key=secrets.token_urlsafe(48))
access = engine.create_access_token({"sub": "gw-user", "tenant_id": "t1"})
refresh = engine.create_refresh_token({"sub": "gw-user", "tenant_id": "t1"})
assert engine.decode_token(access, expected_type="access")["sub"] == "gw-user"
try:
    engine.decode_token(refresh, expected_type="access")
    raise AssertionError("Refresh token accepted as access token!")
except AuthenticationError:
    pass

# 3. SecretStore fail-closed & caching behavior
store = InMemorySecretStore({"api-key": "val-1"})
assert fetch_secret("api-key", store=store) == "val-1"
try:
    fetch_secret("nonexistent-secret", store=store)
    raise AssertionError("Missing secret should raise SecretAccessError!")
except SecretAccessError:
    pass

# 4. Gateway auth_guard rejects unauthenticated caller with invalid token
try:
    auth_guard.authenticate_caller(
        headers={"Authorization": "Bearer invalid.jwt.sig"},
        jwt_engine=engine,
    )
    raise AssertionError("Invalid Bearer token was accepted!")
except AuthenticationError:
    pass

print("  => JWT enforcement, type-check, SecretStore, and auth_guard verified.")
PYEOF

echo "[PASS] Gateway JWT enforcement & secret fail-closed checks passed."
