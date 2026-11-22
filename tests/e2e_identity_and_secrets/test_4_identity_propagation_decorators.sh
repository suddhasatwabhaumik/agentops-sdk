#!/usr/bin/env bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# TEST 4: @pass_user_identity ContextVar Isolation & @inject_secret Overwrite Protection

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

MODE="${MODE:-hermetic}"
if [[ "${1:-}" == "--offline" ]]; then
    MODE="hermetic"
    shift
fi

export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"
export MODE

cd "${REPO_ROOT}"
echo "Running Test 4: Identity Propagation & Secret Injection Decorators (MODE=${MODE})..."

"${PYTHON_BIN}" - <<'PYEOF'
import asyncio
import datetime

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

from agentops_sdk.domain import exceptions
from agentops_sdk.security._jwks import build_entra_issuer
from agentops_sdk.security.auth import JWTAuthEngine
from agentops_sdk.security.identity import (
    get_current_identity,
    get_user_identity_token,
    inject_secret,
    pass_user_identity,
)
from agentops_sdk.security.secrets import InMemorySecretStore

# Build RS256 engine for @pass_user_identity verification
private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode("utf-8")
public_pem = private_key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode("utf-8")

issuer = build_entra_issuer("example-tenant-01")
audience = "agentops-api"
jwt_engine = JWTAuthEngine(public_key=public_pem, audience=audience, issuer=issuer)

now = datetime.datetime.now(datetime.UTC)
token_alice = jwt.encode(
    {
        "sub": "alice@example.com",
        "tenant_id": "tenant-acme",
        "aud": audience,
        "iss": issuer,
        "type": "access",
        "iat": now,
        "exp": now + datetime.timedelta(minutes=5),
    },
    private_pem,
    algorithm="RS256",
)


@pass_user_identity(jwt_engine=jwt_engine)
def sync_op(query: str, auth_token: str | None = None) -> tuple[str, str]:
    scope = get_current_identity()
    return scope.user_id, str(scope.claims.get("tenant_id"))


@pass_user_identity(jwt_engine=jwt_engine)
async def async_op(query: str, auth_token: str | None = None) -> str:
    return get_current_identity().user_id


assert get_user_identity_token() is None
uid, tid = sync_op("Series-700", auth_token=f"Bearer {token_alice}")
assert uid == "alice@example.com"
assert tid == "tenant-acme"
# ContextVar must be cleanly reset after exit
assert get_user_identity_token() is None
assert get_current_identity().user_id == ""

async_uid = asyncio.run(async_op("UNIT-001", auth_token=token_alice))
assert async_uid == "alice@example.com"
assert get_user_identity_token() is None

# Missing token with jwt_engine configured must raise AuthenticationError
try:
    sync_op("UNIT-002")
    raise AssertionError("Expected AuthenticationError when auth_token missing")
except exceptions.AuthenticationError:
    pass

# @inject_secret overwrite protection: caller cannot spoof secret_value
secret_store = InMemorySecretStore({"agentops_db_secret": "real-store-secret-999"})


@inject_secret("agentops_db_secret", param_name="db_secret", store=secret_store)
def use_secret(tenant: str, db_secret: str = "") -> str:
    return f"{tenant}:{db_secret}"


assert use_secret("tenant-acme") == "tenant-acme:real-store-secret-999"
assert (
    use_secret("tenant-acme", db_secret="caller-spoofed-value")
    == "tenant-acme:real-store-secret-999"
)

print("@pass_user_identity ContextVar isolation & @inject_secret overwrite protection verified.")
PYEOF

echo "[PASS] Test 4: Identity Propagation & Secret Injection Decorators validated."
