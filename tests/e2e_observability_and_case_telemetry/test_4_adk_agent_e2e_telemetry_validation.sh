#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 4: ADK Agent E2E Observability & Case Telemetry Validation (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}}"
cd "${REPO_ROOT}"

"${PYTHON_BIN}" "${SCRIPT_DIR}/run_adk_observability_agent.py" \
    --project="${PROJECT_ID}" \
    --dataset="agentops_observability" \
    --local-only

test -f "e2e_case_telemetry.jsonl"
grep -q '"use_case_name": "case_parts_advisor"' "e2e_case_telemetry.jsonl"
grep -q '"transactions_processed": 3' "e2e_case_telemetry.jsonl"
rm -f "e2e_case_telemetry.jsonl"

echo "[PASS] Test 4: ADK Agent E2E Observability validated."
