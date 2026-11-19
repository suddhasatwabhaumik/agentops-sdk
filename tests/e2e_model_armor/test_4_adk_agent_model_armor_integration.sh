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
# TEST 4: ADK Agent Model Armor Security Layer End-to-End Integration (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

EXTRA_FLAGS=()
if [[ "${1:-}" == "--offline" ]]; then
    EXTRA_FLAGS+=(--offline)
    shift
fi
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-}}}"
LOCATION="${2:-${MODEL_ARMOR_LOCATION:-europe-west4}}"
if [[ ${#EXTRA_FLAGS[@]} -eq 0 && -z "${PROJECT_ID}" ]]; then
    EXTRA_FLAGS+=(--offline)
fi

export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

for mode in clean injection pii untrusted_poison; do
    "${PYTHON_BIN}" "${SCRIPT_DIR}/run_adk_model_armor_agent.py" \
        --project-id "${PROJECT_ID}" \
        --location "${LOCATION}" \
        --mode "${mode}" \
        ${EXTRA_FLAGS[@]+"${EXTRA_FLAGS[@]}"}
done

echo "[PASS] Test 4: ADK Agent Model Armor E2E Run succeeded."
