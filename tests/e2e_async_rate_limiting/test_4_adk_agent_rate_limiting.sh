#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 4: ADK Data Science Agent Wrapped with AgentOps v1 Rate Limiting

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}}"

echo "[TEST 4 INFO] Running ADK Data Science Agent Rate Limiting E2E test..."
"${PYTHON_BIN}" "${SCRIPT_DIR}/run_adk_rate_limiting_agent.py" \
    --project "${PROJECT_ID}" \
    --tenant "tenant-adk-datascience" \
    --rpm 4 \
    --burst 3

echo "[TEST 4 PASS] ADK Data Science agent executed cleanly under v1 rate limits."
