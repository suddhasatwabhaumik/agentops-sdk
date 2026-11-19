#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 4: ADK Data Science Agent Tool Validation & OTel Spans (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

EXTRA_FLAGS=()
if [[ "${1:-}" == "--offline" ]]; then
    EXTRA_FLAGS+=(--offline)
    shift
fi
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-}}}"
DATASET_ID="${2:-agent_feature_store}"
TABLE_ID="${3:-customer_features}"
if [[ ${#EXTRA_FLAGS[@]} -eq 0 && -z "${PROJECT_ID}" ]]; then
    EXTRA_FLAGS+=(--offline)
fi

"${PYTHON_BIN}" "${SCRIPT_DIR}/run_adk_validation_agent.py" \
    --project "${PROJECT_ID}" \
    --dataset "${DATASET_ID}" \
    --table "${TABLE_ID}" \
    ${EXTRA_FLAGS[@]+"${EXTRA_FLAGS[@]}"}

echo "[PASS] Test 4: ADK Agent tool validation & OTel spans verified."
