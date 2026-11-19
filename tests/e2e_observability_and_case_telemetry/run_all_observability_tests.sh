#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# MASTER E2E OBSERVABILITY & CASE TELEMETRY TEST SUITE RUNNER (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}}"

TEST_NAMES=(
    "test_1_log_router_provisioning.sh"
    "test_2_use_case_telemetry_export.sh"
    "test_3_cloud_logging_export_utility.sh"
    "test_4_adk_agent_e2e_telemetry_validation.sh"
)

FAILED=0
for idx in "${!TEST_NAMES[@]}"; do
    TEST_FILE="${TEST_NAMES[$idx]}"
    echo -e "\n>>> [$((idx + 1))/${#TEST_NAMES[@]}] Running ${TEST_FILE}..."
    if bash "${SCRIPT_DIR}/${TEST_FILE}" "${PROJECT_ID}"; then
        echo "[PASS] ${TEST_FILE}"
    else
        echo "[FAIL] ${TEST_FILE}"
        FAILED=$((FAILED + 1))
    fi
done

if [[ ${FAILED} -ne 0 ]]; then
    echo "FAILED ${FAILED} observability test(s)."
    exit 1
fi
echo -e "\nALL OBSERVABILITY & CASE TELEMETRY TESTS PASSED."
