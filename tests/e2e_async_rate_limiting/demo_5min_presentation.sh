#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# 5-MINUTE INTERACTIVE DEMO PRESENTATION RUNNER (SDK v1)
# Usage: bash tests/e2e_async_rate_limiting/demo_5min_presentation.sh [--auto] [PROJECT_ID]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
AUTO_MODE=false
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}"

for arg in "$@"; do
    case "$arg" in
        --auto) AUTO_MODE=true ;;
        *) [[ "$arg" != -* ]] && PROJECT_ID="$arg" ;;
    esac
done

pause_or_prompt() {
    local step_title="$1"
    echo ""
    if [[ "${AUTO_MODE}" == "true" ]]; then
        echo ">>> [Auto] Next: ${step_title}..."
        sleep 1
    else
        echo ">>> Press [ENTER] for ${step_title}..."
        read -r
    fi
}

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

echo "==============================================================================="
echo "   AGENTOPS SDK v1: RATE LIMITING, RLS & OTel LIVE DEMO"
echo "   Project: ${PROJECT_ID}"
echo "==============================================================================="

pause_or_prompt "MINUTE 1: JWT Auth Guard & RLS Query Rewriting"
bash "${SCRIPT_DIR}/test_1_jwt_auth_and_rls_security.sh" "${PROJECT_ID}"

pause_or_prompt "MINUTE 2: Multi-Tier Async Rate Limiting & Tenant Isolation"
bash "${SCRIPT_DIR}/test_2_async_rate_limiting_tiers.sh" "${PROJECT_ID}"

pause_or_prompt "MINUTE 3: Concurrency Limits & OpenTelemetry Metrics"
bash "${SCRIPT_DIR}/test_3_concurrency_limits_and_metrics.sh" "${PROJECT_ID}"

pause_or_prompt "MINUTE 4: ADK Data Science Agent Burst Throttling"
"${PYTHON_BIN}" "${SCRIPT_DIR}/run_adk_rate_limiting_agent.py" \
    --project "${PROJECT_ID}" --tenant "tenant-adk-datascience" --rpm 4 --burst 3

echo -e "\nLIVE DEMO COMPLETED SUCCESSFULLY WITH 0 ERRORS."
