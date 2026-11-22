#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# MASTER E2E MEMORY BANK TEST SUITE RUNNER (hermetic|live)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${MODE:-hermetic}"
export MODE

if [[ "${MODE}" != "hermetic" && "${MODE}" != "live" ]]; then
    echo "ERROR: MODE must be 'hermetic' or 'live' (got '${MODE}')."
    exit 1
fi

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-}}}"
if [[ "${MODE}" == "live" && -z "${PROJECT_ID}" ]]; then
    PROJECT_ID="$(gcloud config get-value project 2>/dev/null || echo "")"
fi
if [[ "${MODE}" == "live" && -z "${PROJECT_ID}" ]]; then
    echo "ERROR: GCP Project ID required in MODE=live."
    exit 1
fi
LOCATION="${2:-${GOOGLE_CLOUD_LOCATION:-europe-west4}}"

TEST_NAMES=(
    "test_1_vertex_memory_bank_crud_and_scopes.sh"
    "test_2_semantic_similarity_search.sh"
    "test_3_cloudsql_session_memory_isolation.sh"
    "test_4_composite_memory_adk_agent.sh"
)

FAILED=0
for idx in "${!TEST_NAMES[@]}"; do
    TEST_FILE="${TEST_NAMES[$idx]}"
    echo -e "\n>>> Running [$((idx + 1))/${#TEST_NAMES[@]}] (${MODE}): ${TEST_FILE}"
    if bash "${SCRIPT_DIR}/${TEST_FILE}" "${PROJECT_ID}" "${LOCATION}"; then
        echo ">>> [PASS] ${TEST_FILE}"
    else
        echo ">>> [FAIL] ${TEST_FILE}"
        FAILED=$((FAILED + 1))
    fi
done

if [[ ${FAILED} -ne 0 ]]; then
    echo "FAILED ${FAILED} memory bank E2E test(s)."
    exit 1
fi
echo -e "\nALL 4 MEMORY BANK E2E TESTS PASSED (${MODE})."
