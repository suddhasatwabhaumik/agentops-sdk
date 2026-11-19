#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# MASTER E2E TOOL OUTPUT VALIDATION TEST SUITE RUNNER (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE_ARG=""
if [[ "${1:-}" == "--offline" ]]; then
    MODE_ARG="--offline"
    shift
fi
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-}}}"
DATASET_ID="${2:-agent_feature_store}"
TABLE_ID="${3:-customer_features}"
if [[ -z "${MODE_ARG}" && -z "${PROJECT_ID}" ]]; then
    MODE_ARG="--offline"
fi

TEST_NAMES=(
    "test_1_sync_and_async_validation.sh"
    "test_2_retry_backoff_and_exhaustion.sh"
    "test_3_assertion_callbacks_and_guards.sh"
    "test_4_adk_agent_tool_validation.sh"
)

FAILED=0
for idx in "${!TEST_NAMES[@]}"; do
    TEST_FILE="${TEST_NAMES[$idx]}"
    echo -e "\n>>> Running [$((idx + 1))/${#TEST_NAMES[@]}]: ${TEST_FILE}"
    if [[ "${TEST_FILE}" == "test_4_adk_agent_tool_validation.sh" && -n "${MODE_ARG}" ]]; then
        if bash "${SCRIPT_DIR}/${TEST_FILE}" "${MODE_ARG}"; then
            echo ">>> [PASS] ${TEST_FILE}"
        else
            echo ">>> [FAIL] ${TEST_FILE}"
            FAILED=$((FAILED + 1))
        fi
    else
        if bash "${SCRIPT_DIR}/${TEST_FILE}" "${PROJECT_ID}" "${DATASET_ID}" "${TABLE_ID}"; then
            echo ">>> [PASS] ${TEST_FILE}"
        else
            echo ">>> [FAIL] ${TEST_FILE}"
            FAILED=$((FAILED + 1))
        fi
    fi
done

if [[ ${FAILED} -ne 0 ]]; then
    echo "FAILED ${FAILED} tool validation test(s)."
    exit 1
fi
echo -e "\nALL 4 TOOL VALIDATION E2E TESTS PASSED."
