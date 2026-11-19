#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# MASTER E2E VERTEX AI EVALUATOR TEST SUITE RUNNER (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null || echo "")}}}"
if [[ -z "${PROJECT_ID}" ]]; then
    echo "ERROR: GCP Project ID required. Pass arg 1 or set GOOGLE_CLOUD_PROJECT."
    exit 1
fi
LOCATION="${2:-${GOOGLE_CLOUD_LOCATION:-europe-west4}}"

TEST_NAMES=(
    "test_1_groundedness_and_qa_relevance.sh"
    "test_2_multi_metric_eval_task.sh"
    "test_3_eval_threshold_gating_and_alerts.sh"
    "test_4_adk_agent_live_evaluation.sh"
    "test_5_agentic_trajectory_evaluation.sh"
    "test_6_pluggable_evaluator_experimentation.sh"
)

FAILED=0
for idx in "${!TEST_NAMES[@]}"; do
    TEST_FILE="${TEST_NAMES[$idx]}"
    echo -e "\n>>> Running [$((idx + 1))/${#TEST_NAMES[@]}]: ${TEST_FILE}"
    if bash "${SCRIPT_DIR}/${TEST_FILE}" "${PROJECT_ID}" "${LOCATION}"; then
        echo ">>> [PASS] ${TEST_FILE}"
    else
        echo ">>> [FAIL] ${TEST_FILE}"
        FAILED=$((FAILED + 1))
    fi
done

if [[ ${FAILED} -ne 0 ]]; then
    echo "FAILED ${FAILED} Vertex evaluator test(s)."
    exit 1
fi
echo -e "\nALL 6 VERTEX AI EVALUATOR TESTS PASSED."
