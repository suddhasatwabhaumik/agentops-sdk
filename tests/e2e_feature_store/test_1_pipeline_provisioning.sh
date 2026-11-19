#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# ==============================================================================
# TEST 1: Vertex AI Feature Online Store & Feature View Provisioning Test
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[TEST 1 INFO] $(date +'%Y-%m-%d %H:%M:%S')${NC} $1"; }
log_pass() { echo -e "${GREEN}[TEST 1 PASS] $(date +'%Y-%m-%d %H:%M:%S')${NC} $1"; }

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
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
DATASET_ID="agent_feature_store"
TABLE_ID="customer_features"
STORE_ID="agent_online_store"
VIEW_ID="customer_features_view"

log_info "Running Provisioning Test on Project: ${PROJECT_ID}, Region: ${REGION} (offline=${OFFLINE})..."

log_info "Part A: Validating migration CLI setup logic in dry-run mode..."
"${PYTHON_BIN}" -m tools.feature_store_migration.step2_setup_feature_store \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --dataset "${DATASET_ID}" \
    --table "${TABLE_ID}" \
    --online-store "${STORE_ID}" \
    --feature-view "${VIEW_ID}" \
    --dry-run
log_pass "Part A passed: Dry-run setup succeeded."

if [[ "${OFFLINE}" == "true" ]]; then
    log_info "Part B (--offline): Verifying InMemoryFeatureOnlineStore + FeatureStoreAdminClient import surface..."
    "${PYTHON_BIN}" -c "
from agentops_sdk import FeatureStoreAdminClient, InMemoryFeatureOnlineStore
store = InMemoryFeatureOnlineStore()
store.seed_features('${STORE_ID}', '${VIEW_ID}', {'ENTITY-1001': {'stock_level': 10}})
res = store.read_online_features('${STORE_ID}', '${VIEW_ID}', entity_ids=['ENTITY-1001'])
assert res['ENTITY-1001']['stock_level'] == 10, res
assert FeatureStoreAdminClient is not None
print('Offline Feature Store SDK provisioning contract verified.')
"
else
    log_info "Part B: Verifying live Feature Online Store in ${PROJECT_ID}/${REGION}..."
    "${PYTHON_BIN}" -c "
from google.api_core.client_options import ClientOptions
from google.cloud import aiplatform_v1

client = aiplatform_v1.FeatureOnlineStoreAdminServiceClient(
    client_options=ClientOptions(api_endpoint='${REGION}-aiplatform.googleapis.com')
)
store_path = 'projects/${PROJECT_ID}/locations/${REGION}/featureOnlineStores/${STORE_ID}'
store = client.get_feature_online_store(name=store_path)
print(f'Found live store: {store.name}, state={store.state}')
"
fi
log_pass "Part B passed: Feature Store provisioning check completed."
exit 0
