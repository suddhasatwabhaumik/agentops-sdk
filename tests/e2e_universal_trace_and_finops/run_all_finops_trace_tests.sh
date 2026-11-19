#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# MASTER E2E UNIVERSAL TRACE & FINOPS TEST SUITE RUNNER (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE_ARG=""
if [[ "${1:-}" == "--offline" ]]; then
    MODE_ARG="--offline"
    shift
fi
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}}"
DATASET_ID="${2:-agent_feature_store}"
TABLE_ID="${3:-customer_features}"
if [[ -z "${MODE_ARG}" && "${PROJECT_ID}" == "local-offline" ]]; then
    MODE_ARG="--offline"
fi

TEST_NAMES=(
    "test_1_universal_agent_flow_tracing.sh"
    "test_2_finops_token_economics_and_pricing.sh"
    "test_3_cloud_logging_and_trace_correlation.sh"
    "test_4_adk_agent_live_finops_trace.sh"
)

FAILED=0
for idx in "${!TEST_NAMES[@]}"; do
    TEST_FILE="${TEST_NAMES[$idx]}"
    echo -e "\n>>> [$((idx + 1))/${#TEST_NAMES[@]}] Running ${TEST_FILE}..."
    if [[ "${TEST_FILE}" == "test_4_adk_agent_live_finops_trace.sh" && -n "${MODE_ARG}" ]]; then
        if bash "${SCRIPT_DIR}/${TEST_FILE}" "${MODE_ARG}"; then
            echo "[PASS] ${TEST_FILE}"
        else
            echo "[FAIL] ${TEST_FILE}"
            FAILED=$((FAILED + 1))
        fi
    else
        if bash "${SCRIPT_DIR}/${TEST_FILE}" "${PROJECT_ID}" "${DATASET_ID}" "${TABLE_ID}"; then
            echo "[PASS] ${TEST_FILE}"
        else
            echo "[FAIL] ${TEST_FILE}"
            FAILED=$((FAILED + 1))
        fi
    fi
done

if [[ ${FAILED} -ne 0 ]]; then
    echo "FAILED ${FAILED} FinOps trace test(s)."
    exit 1
fi
echo -e "\nALL 4 UNIVERSAL TRACE & FINOPS TESTS PASSED."
