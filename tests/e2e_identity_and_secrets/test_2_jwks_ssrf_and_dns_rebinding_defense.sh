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
# TEST 2: JWKS SSRF, IP Range, Host Allowlist, Redirect & DNS Rebinding Defense

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
echo "Running Test 2: JWKS SSRF & DNS Rebinding Defense (MODE=${MODE})..."

"${PYTHON_BIN}" - <<'PYEOF'
import socket
from unittest.mock import MagicMock, patch
import urllib.request

from agentops_sdk.domain import exceptions
from agentops_sdk.security._jwks import SafeJWKClient, validate_jwks_url


def _assert_blocked(url: str, *, allowed_hosts=None) -> None:
    try:
        validate_jwks_url(url, allowed_hosts=allowed_hosts)
        raise AssertionError(f"Expected AuthenticationError for blocked URL: {url}")
    except exceptions.AuthenticationError:
        pass


# 1. Non-HTTPS (HTTP) blocked
_assert_blocked("http://agentops-idp.example.com/.well-known/jwks.json",
                allowed_hosts=["agentops-idp.example.com"])

# 2. Loopback, private, link-local, and CGNAT IP literals blocked
for ip in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "100.64.0.1", "192.168.1.10"):
    _assert_blocked(f"https://{ip}/jwks.json", allowed_hosts=[ip])

# 3. Host allowlist enforcement
fake_public_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
with patch("socket.getaddrinfo", return_value=fake_public_dns):
    ok_url = validate_jwks_url(
        "https://agentops-idp.example.com/jwks.json",
        allowed_hosts=["agentops-idp.example.com"],
    )
    assert ok_url == "https://agentops-idp.example.com/jwks.json"
    _assert_blocked(
        "https://untrusted.example.org/jwks.json",
        allowed_hosts=["agentops-idp.example.com"],
    )

# 4. DNS rebinding defense: first validation passes (public IP), fetch re-resolves to private IP
client = SafeJWKClient(
    "https://agentops-idp.example.com/jwks.json",
    allowed_hosts=["agentops-idp.example.com"],
)
rebound_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))]
with patch("socket.getaddrinfo", return_value=rebound_dns):
    try:
        client.fetch_data()
        raise AssertionError("Expected AuthenticationError on DNS rebinding to link-local IP")
    except exceptions.AuthenticationError:
        pass

# 5. Redirect defense: _NoRedirectHandler rejects HTTP 302 redirects
with patch("socket.getaddrinfo", return_value=fake_public_dns):
    with patch("urllib.request.OpenerDirector.open") as mock_open:
        mock_open.side_effect = urllib.error.HTTPError(
            "https://agentops-idp.example.com/jwks.json",
            302,
            "JWKS redirects are disabled.",
            {},
            None,
        )
        try:
            client.fetch_data()
            raise AssertionError("Expected error on HTTP redirect")
        except Exception:
            pass

print("JWKS SSRF, IP range, allowlist, redirect & DNS rebinding defenses verified.")
PYEOF

echo "[PASS] Test 2: JWKS SSRF & DNS Rebinding Defense validated."
