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
# TEST 1: RS256 PEM & JWKS, build_entra_issuer, aud/iss & alg Enforcement

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
echo "Running Test 1: RS256, aud/iss & alg Enforcement (MODE=${MODE})..."

"${PYTHON_BIN}" - "${MODE}" <<'PYEOF'
import base64
import datetime
import json
import socket
import sys
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

from agentops_sdk.domain import exceptions
from agentops_sdk.security._jwks import build_entra_issuer
from agentops_sdk.security.auth import JWTAuthEngine

mode = sys.argv[1]

# Generate ephemeral RSA keypair for hermetic RS256 validation
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
assert issuer == "https://login.microsoftonline.com/example-tenant-01/v2.0"
audience = "agentops-api"

now = datetime.datetime.now(datetime.UTC)
valid_claims = {
    "sub": "analyst@example.com",
    "tenant_id": "tenant-acme",
    "aud": audience,
    "iss": issuer,
    "type": "access",
    "iat": now,
    "exp": now + datetime.timedelta(minutes=5),
}
rs256_token = jwt.encode(
    valid_claims, private_pem, algorithm="RS256", headers={"kid": "kid-01"}
)

# 1. RS256 PEM engine validation
pem_engine = JWTAuthEngine(
    public_key=public_pem,
    audience=audience,
    issuer=issuer,
)
decoded = pem_engine.decode_token(rs256_token, expected_type="access")
assert decoded["sub"] == "analyst@example.com"
assert decoded["tenant_id"] == "tenant-acme"

# 2. Wrong aud / wrong iss rejection
wrong_aud_token = jwt.encode(
    {**valid_claims, "aud": "wrong-api"}, private_pem, algorithm="RS256"
)
wrong_iss_token = jwt.encode(
    {**valid_claims, "iss": "https://evil.example.org/v2.0"},
    private_pem,
    algorithm="RS256",
)
for bad_tok in (wrong_aud_token, wrong_iss_token):
    try:
        pem_engine.decode_token(bad_tok, expected_type="access")
        raise AssertionError("Expected AuthenticationError for bad aud/iss")
    except exceptions.AuthenticationError:
        pass

# 3. alg: none rejection
header_b64 = base64.urlsafe_b64encode(
    json.dumps({"alg": "none", "typ": "JWT"}).encode()
).rstrip(b"=").decode()
payload_b64 = base64.urlsafe_b64encode(
    json.dumps({
        "sub": "analyst@example.com",
        "aud": audience,
        "iss": issuer,
        "type": "access",
        "exp": int((now + datetime.timedelta(minutes=5)).timestamp()),
    }).encode()
).rstrip(b"=").decode()
alg_none_token = f"{header_b64}.{payload_b64}."
try:
    pem_engine.decode_token(alg_none_token, expected_type="access")
    raise AssertionError("Expected AuthenticationError for alg: none")
except exceptions.AuthenticationError:
    pass

# 4. HS256 / RS256 algorithm confusion downgrade rejection
import hashlib
import hmac as _hmac
hs_header = base64.urlsafe_b64encode(
    json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
).rstrip(b"=").decode()
hs_signing_input = f"{hs_header}.{payload_b64}".encode()
hs_sig = base64.urlsafe_b64encode(
    _hmac.new(public_pem.encode("utf-8"), hs_signing_input, hashlib.sha256).digest()
).rstrip(b"=").decode()
hs_downgrade = f"{hs_header}.{payload_b64}.{hs_sig}"
try:
    pem_engine.decode_token(hs_downgrade, expected_type="access")
    raise AssertionError("Expected AuthenticationError for HS256 downgrade")
except exceptions.AuthenticationError:
    pass

# 5. JWKS URL engine (hermetic DNS + signing key mock)
if mode == "hermetic":
    fake_addr = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    with patch("socket.getaddrinfo", return_value=fake_addr):
        jwks_engine = JWTAuthEngine(
            jwks_url="https://agentops-idp.example.com/.well-known/jwks.json",
            audience=audience,
            issuer=issuer,
            allowed_jwks_hosts=["agentops-idp.example.com"],
        )
    with patch.object(
        jwks_engine._jwks_client,
        "get_signing_key_from_jwt",
        return_value=SimpleNamespace(key=private_key.public_key()),
    ):
        jwks_decoded = jwks_engine.decode_token(rs256_token, expected_type="access")
        assert jwks_decoded["sub"] == "analyst@example.com"

print("RS256 PEM, JWKS, aud/iss, and alg enforcement verified.")
PYEOF

echo "[PASS] Test 1: RS256, aud/iss & alg Enforcement validated."
