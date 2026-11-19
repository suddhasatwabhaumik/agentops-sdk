#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 3: Developer Cloud Logging Export Utility Validation (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null || echo "")}}}"
if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "local-offline" ]]; then
    echo "[SKIP] Test 3 requires a live GCP Project ID for Cloud Logging export."
    exit 0
fi

export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

cd "${REPO_ROOT}"
EXPORT_FILE="tests/e2e_observability_and_case_telemetry/test_export.jsonl"
"${PYTHON_BIN}" -m tools.observability.log_router_admin \
    --project="${PROJECT_ID}" \
    export --hours=12 --limit=10 --no-bq --no-gcs --output-file="${EXPORT_FILE}"
test -f "${EXPORT_FILE}"

OUTPUT_FILE="tests/e2e_observability_and_case_telemetry/test_wrapper_export.jsonl" \
HOURS=1 LIMIT=5 GOOGLE_CLOUD_PROJECT="${PROJECT_ID}" \
    bash "${REPO_ROOT}/tools/observability/export_logs.sh"
test -f "tests/e2e_observability_and_case_telemetry/test_wrapper_export.jsonl"
rm -f "${EXPORT_FILE}" "tests/e2e_observability_and_case_telemetry/test_wrapper_export.jsonl"
echo "[PASS] Test 3: Developer Cloud Logging export utility validated."
