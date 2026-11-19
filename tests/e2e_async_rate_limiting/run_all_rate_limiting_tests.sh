#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# MASTER E2E ASYNC RATE LIMITING & CONCURRENCY TEST SUITE RUNNER (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}}"

echo "================================================================="
echo " MASTER E2E ASYNC RATE LIMITING & CONCURRENCY TEST SUITE (v1)"
echo " Project ID: ${PROJECT_ID}"
echo "================================================================="

TEST_NAMES=(
    "test_1_jwt_auth_and_rls_security.sh"
    "test_2_async_rate_limiting_tiers.sh"
    "test_3_concurrency_limits_and_metrics.sh"
    "test_4_adk_agent_rate_limiting.sh"
)

FAILED=0
for idx in "${!TEST_NAMES[@]}"; do
    TEST_FILE="${TEST_NAMES[$idx]}"
    echo -e "\n>>> Running [$((idx + 1))/${#TEST_NAMES[@]}]: ${TEST_FILE}"
    if bash "${SCRIPT_DIR}/${TEST_FILE}" "${PROJECT_ID}"; then
        echo ">>> [PASS] ${TEST_FILE}"
    else
        echo ">>> [FAIL] ${TEST_FILE}"
        FAILED=$((FAILED + 1))
    fi
done

if [[ ${FAILED} -ne 0 ]]; then
    echo "FAILED ${FAILED} rate limiting suite test(s)."
    exit 1
fi
echo -e "\nALL 4 RATE LIMITING E2E TESTS PASSED."
