#!/usr/bin/env bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# MASTER E2E CI/CD EVALUATION QUALITY GATES TEST SUITE RUNNER (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}}"
LOCATION="${2:-europe-west4}"

TEST_NAMES=(
    "test_1_mock_quality_gate.sh"
    "test_2_golden_benchmark_eval.sh"
    "test_3_trajectory_agentic_gate.sh"
    "test_4_adk_agent_quality_gate_integration.sh"
)

FAILED=0
for idx in "${!TEST_NAMES[@]}"; do
    TEST_FILE="${TEST_NAMES[$idx]}"
    echo -e "\n>>> Executing Test $((idx + 1))/${#TEST_NAMES[@]}: ${TEST_FILE}"
    if bash "${SCRIPT_DIR}/${TEST_FILE}" "${PROJECT_ID}" "${LOCATION}"; then
        echo "==> PASS: ${TEST_FILE}"
    else
        echo "==> FAIL: ${TEST_FILE}"
        FAILED=$((FAILED + 1))
    fi
done

if [[ ${FAILED} -ne 0 ]]; then
    echo "FAILED ${FAILED} CI/CD quality gate test(s)."
    exit 1
fi
echo -e "\nALL 4 CI/CD QUALITY GATE TESTS PASSED."
