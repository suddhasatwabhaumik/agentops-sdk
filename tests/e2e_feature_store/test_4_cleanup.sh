#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# ==============================================================================
# TEST 4: Resource Inspection, Unit Tests & Cleanup Verification
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[TEST 4 INFO] $(date +'%Y-%m-%d %H:%M:%S')${NC} $1"; }
log_pass() { echo -e "${GREEN}[TEST 4 PASS] $(date +'%Y-%m-%d %H:%M:%S')${NC} $1"; }

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
PYTEST_BIN="${REPO_ROOT}/.venv/bin/pytest"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
if [[ ! -f "${PYTEST_BIN}" ]]; then
    PYTEST_BIN="pytest"
fi

export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

OFFLINE=false
if [[ "${1:-}" == "--offline" ]]; then
    OFFLINE=true
    shift
fi

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null || echo "")}}}"
if [[ -z "${PROJECT_ID}" ]]; then
    if [[ "${OFFLINE}" == "true" ]]; then
        PROJECT_ID="local-offline"
    else
        echo "ERROR: GCP Project ID is required unless --offline is specified."
        exit 1
    fi
fi
REGION="${2:-europe-west4}"
STORE_ID="agent_online_store"
DELETE_FLAG="${3:-false}"

log_info "Part A: Running Feature Store unit & CLI migration tests..."
"${PYTEST_BIN}" -q \
    "${REPO_ROOT}/tests/unit/test_feature_store.py" \
    "${REPO_ROOT}/tests/unit/test_tools_feature_store.py"
log_pass "Part A passed: Unit test suite green."

if [[ "${OFFLINE}" == "true" ]]; then
    log_info "Part B (--offline): Live Vertex AI cleanup skipped in offline mode."
else
    log_info "Part B: Inspecting Feature Store resources in ${PROJECT_ID}/${REGION}..."
    "${PYTHON_BIN}" -c "
from google.api_core.client_options import ClientOptions
from google.cloud import aiplatform_v1

client = aiplatform_v1.FeatureOnlineStoreAdminServiceClient(
    client_options=ClientOptions(api_endpoint='${REGION}-aiplatform.googleapis.com')
)
parent = 'projects/${PROJECT_ID}/locations/${REGION}'
stores = list(client.list_feature_online_stores(parent=parent))
print(f'Active Online Stores in {parent}: {[s.name for s in stores]}')
if '${DELETE_FLAG}' == '--delete':
    for store in stores:
        if '${STORE_ID}' in store.name:
            print(f'Deleting test store: {store.name}...')
            client.delete_feature_online_store(name=store.name, force=True)
            print(f'Deleted {store.name}.')
"
fi

log_pass "TEST 4 COMPLETED: Resource inspection & Feature Store verification passed."
exit 0
