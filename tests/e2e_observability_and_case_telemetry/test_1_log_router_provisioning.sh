#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 1: Log Router Provisioning & Admin CLI Utility Validation (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
PYTEST_BIN="${REPO_ROOT}/.venv/bin/pytest"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
    PYTEST_BIN="pytest"
fi

export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}}"
echo "=== [TEST 1] Log Router Provisioning & Admin Utilities (${PROJECT_ID}) ==="

cd "${REPO_ROOT}"
"${PYTHON_BIN}" -m tools.observability.log_router_admin --help >/dev/null
"${PYTHON_BIN}" -m tools.observability.log_router_admin setup --help >/dev/null
"${PYTHON_BIN}" -m tools.observability.log_router_admin export --help >/dev/null

bash -n "${REPO_ROOT}/tools/observability/setup_log_routers.sh"
bash -n "${REPO_ROOT}/tools/observability/export_logs.sh"
test -x "${REPO_ROOT}/tools/observability/setup_log_routers.sh"
test -x "${REPO_ROOT}/tools/observability/export_logs.sh"

"${PYTEST_BIN}" -v tests/unit/test_tools_cli.py
echo "[PASS] Test 1: Log Router CLI & unit tests passed."
