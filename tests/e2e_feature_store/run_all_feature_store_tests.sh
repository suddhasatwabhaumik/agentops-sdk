#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# ==============================================================================
# MASTER E2E FEATURE STORE TEST SUITE RUNNER (SDK v1)
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

MODE_FLAG=()
if [[ "${1:-}" == "--offline" ]]; then
    MODE_FLAG=("--offline")
    shift
    PROJECT_ID="${1:-local-offline}"
else
    PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null || echo "")}}}"
fi

if [[ -z "${PROJECT_ID}" ]]; then
    echo "ERROR: GCP Project ID is required ($0 <PROJECT_ID> [REGION] or $0 --offline)."
    exit 1
fi
REGION="${2:-europe-west4}"

echo -e "${CYAN}=================================================================${NC}"
echo -e "${CYAN}   MASTER E2E FEATURE STORE TEST SUITE: VERTEX AI V2 + SDK V1    ${NC}"
echo -e "${CYAN}=================================================================${NC}"
echo -e "Project ID: ${BOLD}${PROJECT_ID}${NC}"
echo -e "Region:     ${BOLD}${REGION}${NC}"
echo -e "Mode:       ${BOLD}${MODE_FLAG[*]:-live_gcp}${NC}"
echo -e "${CYAN}-----------------------------------------------------------------${NC}"

declare -a TEST_NAMES=(
    "test_1_pipeline_provisioning.sh"
    "test_2_data_sync_and_schedule.sh"
    "test_3_adk_agent_integration.sh"
    "test_4_cleanup.sh"
)

declare -a TEST_DESCRIPTIONS=(
    "Vertex AI Online Store & View Provisioning"
    "BigQuery Ingestion Sync & In-Engine Schedule"
    "ADK Data Science Agent Integration & Lookup"
    "Unit Tests & Resource Inspection Verification"
)

TOTAL_TESTS="${#TEST_NAMES[@]}"
PASSED_TESTS=0
FAILED_TESTS=0
declare -a TEST_RESULTS=()

for i in "${!TEST_NAMES[@]}"; do
    TEST_FILE="${TEST_NAMES[$i]}"
    TEST_DESC="${TEST_DESCRIPTIONS[$i]}"
    TEST_PATH="${SCRIPT_DIR}/${TEST_FILE}"
    echo -e "\n${BLUE}>>> Running [Test $((i+1))/${TOTAL_TESTS}]: ${TEST_DESC} (${TEST_FILE})...${NC}"
    if bash "${TEST_PATH}" ${MODE_FLAG[@]+"${MODE_FLAG[@]}"} "${PROJECT_ID}" "${REGION}"; then
        echo -e "${GREEN}>>> [PASS] ${TEST_DESC}${NC}"
        TEST_RESULTS+=("PASS")
        PASSED_TESTS=$((PASSED_TESTS + 1))
    else
        echo -e "${RED}>>> [FAIL] ${TEST_DESC}${NC}"
        TEST_RESULTS+=("FAIL")
        FAILED_TESTS=$((FAILED_TESTS + 1))
    fi
done

echo -e "\n${CYAN}=================================================================${NC}"
printf "%-6s | %-45s | %-10s\n" "Test #" "Description" "Result"
echo "-----------------------------------------------------------------"
for i in "${!TEST_NAMES[@]}"; do
    STATUS="${TEST_RESULTS[$i]}"
    COLOR="${RED}"
    [[ "${STATUS}" == "PASS" ]] && COLOR="${GREEN}"
    printf "%-6s | %-45s | ${COLOR}%-10s${NC}\n" "$((i+1))" "${TEST_DESCRIPTIONS[$i]}" "${STATUS}"
done
echo "-----------------------------------------------------------------"
echo -e "Total: ${TOTAL_TESTS} | Passed: ${PASSED_TESTS} | Failed: ${FAILED_TESTS}"
echo -e "${CYAN}=================================================================${NC}"

if [[ "${FAILED_TESTS}" -gt 0 ]]; then
    exit 1
fi
exit 0
