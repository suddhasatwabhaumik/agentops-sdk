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
# MASTER E2E IDENTITY, JWT/JWKS & SECRETS TEST SUITE RUNNER (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${MODE:-hermetic}"
if [[ "${1:-}" == "--offline" ]]; then
    MODE="hermetic"
    shift
elif [[ "${1:-}" == "--live" ]]; then
    MODE="live"
    shift
fi

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}}"
LOCATION="${2:-europe-west4}"
export MODE

TEST_NAMES=(
    "test_1_rs256_aud_iss_and_alg_enforcement.sh"
    "test_2_jwks_ssrf_and_dns_rebinding_defense.sh"
    "test_3_secret_manager_token_store.sh"
    "test_4_identity_propagation_decorators.sh"
)

FAILED=0
for idx in "${!TEST_NAMES[@]}"; do
    TEST_FILE="${TEST_NAMES[$idx]}"
    echo -e "\n>>> Executing Test $((idx + 1))/${#TEST_NAMES[@]}: ${TEST_FILE} (MODE=${MODE})"
    if MODE="${MODE}" bash "${SCRIPT_DIR}/${TEST_FILE}" "${PROJECT_ID}" "${LOCATION}"; then
        echo "==> PASS: ${TEST_FILE}"
    else
        echo "==> FAIL: ${TEST_FILE}"
        FAILED=$((FAILED + 1))
    fi
done

if [[ ${FAILED} -ne 0 ]]; then
    echo "FAILED ${FAILED} identity and secrets test(s)."
    exit 1
fi
echo -e "\nALL 4 IDENTITY AND SECRETS TESTS PASSED (MODE=${MODE})."
