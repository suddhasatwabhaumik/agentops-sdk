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
# TEST 3: SecretManagerStore Lifecycle, validate_secret_id, BoundedTTLCache & Revocation

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
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}}"

export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"
export MODE

cd "${REPO_ROOT}"
echo "Running Test 3: SecretManagerStore & BoundedTTLCache (MODE=${MODE})..."

"${PYTHON_BIN}" - "${MODE}" "${PROJECT_ID}" <<'PYEOF'
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from agentops_sdk.domain import exceptions, lifecycle
from agentops_sdk.security.secrets import (
    InMemorySecretStore,
    SecretManagerStore,
    validate_secret_id,
)

mode = sys.argv[1]
project_id = sys.argv[2]

# 1. validate_secret_id path-traversal protection
assert validate_secret_id("agentops_api_key-01") == "agentops_api_key-01"
for bad_id in ("../etc/passwd", "tenant/secret", "", "bad id!"):
    try:
        validate_secret_id(bad_id)
        raise AssertionError(f"Expected SecretAccessError for {bad_id!r}")
    except exceptions.SecretAccessError:
        pass

# 2. BoundedTTLCache maxsize eviction & TTL expiry
cache: lifecycle.BoundedTTLCache[str, str] = lifecycle.BoundedTTLCache(
    maxsize=2, ttl_seconds=0.2
)
cache.set("k1", "v1")
cache.set("k2", "v2")
cache.set("k3", "v3")
assert cache.get("k1") is None, "Expected LRU/maxsize eviction of k1"
assert cache.get("k2") == "v2"
assert cache.get("k3") == "v3"
time.sleep(0.25)
assert cache.get("k2") is None, "Expected TTL expiration of k2"

# 3. SecretManagerStore lifecycle & revocation eviction
if mode == "hermetic":
    mock_sm = MagicMock()
    mock_sm.access_secret_version.return_value = SimpleNamespace(
        payload=SimpleNamespace(data=b"secret-val-v1")
    )
    store = SecretManagerStore(
        project_id="example-tenant-01",
        ttl_seconds=60.0,
        client=mock_sm,
    )
    val1 = store.get_secret("agentops_api_key-01")
    val2 = store.get_secret("agentops_api_key-01")
    assert val1 == "secret-val-v1" and val2 == "secret-val-v1"
    assert mock_sm.access_secret_version.call_count == 1

    # Evict (revocation) forces fresh backend fetch
    mock_sm.access_secret_version.return_value = SimpleNamespace(
        payload=SimpleNamespace(data=b"secret-val-v2")
    )
    store.evict("agentops_api_key-01")
    val3 = store.get_secret("agentops_api_key-01")
    assert val3 == "secret-val-v2"
    assert mock_sm.access_secret_version.call_count == 2

    # InMemorySecretStore roundtrip
    mem_store = InMemorySecretStore({"tenant-acme-key": "acme-token-001"})
    assert mem_store.get_secret("tenant-acme-key") == "acme-token-001"
    mem_store.put_secret("tenant-acme-key", "acme-token-002")
    assert mem_store.get_secret("tenant-acme-key") == "acme-token-002"
    assert mem_store.delete_secret("tenant-acme-key") is True
else:
    if not project_id or project_id == "local-offline":
        print("[SKIP] Live SecretManagerStore test requires a valid GCP project_id.")
        sys.exit(0)
    store = SecretManagerStore(project_id=project_id)
    store.close()

print("SecretManagerStore lifecycle, validate_secret_id, BoundedTTLCache & eviction verified.")
PYEOF

echo "[PASS] Test 3: SecretManagerStore & BoundedTTLCache validated."
