#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# ==============================================================================
# TEST 3: ADK Data Science Multi-Agent Integration & Feature Store Benchmark
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[TEST 3 INFO] $(date +'%Y-%m-%d %H:%M:%S')${NC} $1"; }
log_pass() { echo -e "${GREEN}[TEST 3 PASS] $(date +'%Y-%m-%d %H:%M:%S')${NC} $1"; }

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

OFFLINE_FLAG=()
if [[ "${1:-}" == "--offline" ]]; then
    OFFLINE_FLAG=("--offline")
    shift
    PROJECT_ID="${1:-local-offline}"
else
    PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null || echo "")}}}"
fi

if [[ -z "${PROJECT_ID}" ]]; then
    echo "ERROR: GCP Project ID is required unless --offline is specified."
    exit 1
fi
REGION="${2:-europe-west4}"
STORE_ID="agent_online_store"
VIEW_ID="customer_features_view"
DATASET_ID="agent_feature_store"
TABLE_ID="customer_features"

log_info "Running ADK Data Science Agent Feature Store v1 Integration Test..."
log_info "Target Project: ${PROJECT_ID}, Region: ${REGION}"

"${PYTHON_BIN}" "${SCRIPT_DIR}/run_adk_feature_store_agent.py" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --online-store "${STORE_ID}" \
    --feature-view "${VIEW_ID}" \
    --dataset "${DATASET_ID}" \
    --table "${TABLE_ID}" \
    --entities "ENTITY-1001" "ENTITY-1002" "ENTITY-1003" \
    ${OFFLINE_FLAG[@]+"${OFFLINE_FLAG[@]}"}

log_pass "TEST 3 COMPLETED SUCCESSFULLY: ADK Agent Feature Store v1 lookups verified."
exit 0
