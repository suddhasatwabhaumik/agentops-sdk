#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# MASTER E2E PROMPT REGISTRY TEST SUITE RUNNER (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}}"
LOCATION="${2:-${GOOGLE_CLOUD_LOCATION:-europe-west4}}"

TEST_NAMES=(
    "test_1_git_local_backend.sh"
    "test_2_vertex_ai_prompt_management.sh"
    "test_3_semver_and_template_rendering.sh"
    "test_4_adk_agent_e2e_validation.sh"
)

FAILED=0
for idx in "${!TEST_NAMES[@]}"; do
    TEST_FILE="${TEST_NAMES[$idx]}"
    echo -e "\n>>> Executing [$((idx + 1))/${#TEST_NAMES[@]}]: ${TEST_FILE}"
    if bash "${SCRIPT_DIR}/${TEST_FILE}" "${PROJECT_ID}" "${LOCATION}"; then
        echo "==> PASS: ${TEST_FILE}"
    else
        echo "==> FAIL: ${TEST_FILE}"
        FAILED=$((FAILED + 1))
    fi
done

if [[ ${FAILED} -ne 0 ]]; then
    echo "FAILED ${FAILED} Prompt Registry test(s)."
    exit 1
fi
echo -e "\nALL 4 PROMPT REGISTRY TESTS PASSED."
