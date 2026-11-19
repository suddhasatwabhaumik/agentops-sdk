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
# TEST 1: Offline In-Memory Quality Gate & Markdown Report Generation (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}}"
LOCATION="${2:-europe-west4}"

cd "${REPO_ROOT}"

PYTEST_BIN="${REPO_ROOT}/.venv/bin/pytest"
if [[ ! -f "${PYTEST_BIN}" ]]; then
    PYTEST_BIN="pytest"
fi

REPORT_REL=".pytest_cache/e2e_qgate_summary.md"
REPORT_PATH="${REPO_ROOT}/${REPORT_REL}"
rm -f "${REPORT_PATH}"

echo "Running Test 1: In-Memory Quality Gate & Report Generation..."
"${PYTEST_BIN}" examples/tutorial/13_evaluation_quality_gates/test_quality_gate.py \
    -v \
    -m quality_gate \
    --eval-mode=in_memory \
    --fail-under-groundedness=0.80 \
    --fail-under-relevance=0.80 \
    --eval-report-path="${REPORT_REL}" \
    --eval-project="${PROJECT_ID}" \
    --eval-location="${LOCATION}"

if [[ ! -f "${REPORT_PATH}" ]]; then
    echo "[FAIL] Expected evaluation summary report not found at ${REPORT_PATH}"
    exit 1
fi

grep -q "# AgentOps Quality Gate Evaluation Summary" "${REPORT_PATH}"
grep -q "in_memory" "${REPORT_PATH}"
grep -q "series_700_grounded_answer" "${REPORT_PATH}"
rm -f "${REPORT_PATH}"

echo "[PASS] Test 1: In-Memory Quality Gate & Report Generation succeeded."
