#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 4: ADK Agent Live Evaluation Runner (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null || echo "")}}}"
if [[ -z "${PROJECT_ID}" ]]; then
    echo "ERROR: GCP Project ID required. Pass arg 1 or set GOOGLE_CLOUD_PROJECT."
    exit 1
fi
LOCATION="${2:-${GOOGLE_CLOUD_LOCATION:-europe-west4}}"
EVAL_MODE="${3:-vertex}"

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

RUNNER_SCRIPT="${SCRIPT_DIR}/run_adk_evaluator_agent.py"

echo "Executing ADK Agent live evaluation runner in ${PROJECT_ID}/${LOCATION} (${EVAL_MODE})..."
"${PYTHON_BIN}" "${RUNNER_SCRIPT}" \
    --project "${PROJECT_ID}" \
    --location "${LOCATION}" \
    --eval-mode "${EVAL_MODE}" \
    --mode all

JSON_OUTPUT=$("${PYTHON_BIN}" "${RUNNER_SCRIPT}" \
    --project "${PROJECT_ID}" \
    --location "${LOCATION}" \
    --eval-mode "${EVAL_MODE}" \
    --mode all \
    --json-output)
echo "${JSON_OUTPUT}" | "${PYTHON_BIN}" -c '
import json, sys
rows = {row["scenario"]: row for row in json.load(sys.stdin)}
assert rows["compliant"]["is_passing"] is True, rows["compliant"]
assert not rows["hallucinated"]["is_passing"], rows["hallucinated"]
assert not rows["irrelevant"]["is_passing"], rows["irrelevant"]
'
echo "[PASS] Test 4: ADK Agent live Vertex evaluation verified."
