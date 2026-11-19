#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 4: ADK Prompt Registry Agent End-to-End Validation (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}}"
LOCATION="${2:-${GOOGLE_CLOUD_LOCATION:-europe-west4}}"

echo "Executing ADK Agent with Local YAML backend..."
"${PYTHON_BIN}" "${SCRIPT_DIR}/run_prompt_registry_agent.py" \
    --backend "local" \
    --local-dir "${SCRIPT_DIR}/prompt_examples"

if [[ -n "${PROJECT_ID}" && "${PROJECT_ID}" != "local-offline" ]]; then
    echo "Executing ADK Agent with Vertex AI backend (${PROJECT_ID}/${LOCATION})..."
    "${PYTHON_BIN}" "${SCRIPT_DIR}/run_prompt_registry_agent.py" \
        --backend "vertex_ai" \
        --project-id "${PROJECT_ID}" \
        --location "${LOCATION}" \
        --local-dir "${SCRIPT_DIR}/prompt_examples"
fi

echo "[PASS] Test 4: ADK Prompt Registry Agent E2E validation succeeded."
