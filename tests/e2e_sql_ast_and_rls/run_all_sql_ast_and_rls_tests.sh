#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# MASTER E2E SQL AST & RLS TEST SUITE RUNNER (hermetic|live)

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

TEST_NAMES=(
    "test_1_sql_ast_allowlist_and_ddl_blocking.sh"
    "test_2_rls_joins_ctes_and_subqueries.sh"
    "test_3_dialect_transpilation_and_table_functions.sh"
    "test_4_decorator_and_bigquery_rls_execution.sh"
)

FAILED=0
for idx in "${!TEST_NAMES[@]}"; do
    TEST_FILE="${TEST_NAMES[$idx]}"
    echo -e "\n>>> Running [$((idx + 1))/${#TEST_NAMES[@]}] (${MODE}): ${TEST_FILE}"
    if bash "${SCRIPT_DIR}/${TEST_FILE}" "${PROJECT_ID}"; then
        echo ">>> [PASS] ${TEST_FILE}"
    else
        echo ">>> [FAIL] ${TEST_FILE}"
        FAILED=$((FAILED + 1))
    fi
done

if [[ ${FAILED} -ne 0 ]]; then
    echo "FAILED ${FAILED} SQL AST & RLS E2E test(s)."
    exit 1
fi
echo -e "\nALL 4 SQL AST & RLS E2E TESTS PASSED (${MODE})."
