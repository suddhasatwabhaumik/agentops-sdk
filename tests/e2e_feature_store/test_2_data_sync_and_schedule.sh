#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# ==============================================================================
# TEST 2: Ingestion Sync & In-Engine Zero-Compute Scheduling Test
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[TEST 2 INFO] $(date +'%Y-%m-%d %H:%M:%S')${NC} $1"; }
log_pass() { echo -e "${GREEN}[TEST 2 PASS] $(date +'%Y-%m-%d %H:%M:%S')${NC} $1"; }

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

if [[ "${1:-}" == "--offline" ]]; then
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

log_info "Running Data Sync and Schedule Test on Project: ${PROJECT_ID}, Region: ${REGION}..."

log_info "Part A: Triggering Feature View Ingestion Sync (dry-run)..."
"${PYTHON_BIN}" -m tools.feature_store_migration.step3_trigger_sync \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --online-store "${STORE_ID}" \
    --feature-view "${VIEW_ID}" \
    --dry-run
log_pass "Part A passed: Sync triggering dry-run operational."

TEST_CRON="0 */6 * * *"
log_info "Part B: Setting in-engine sync schedule to '${TEST_CRON}' (dry-run)..."
"${PYTHON_BIN}" -m tools.feature_store_migration.step4_manage_sync_schedule \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --online-store "${STORE_ID}" \
    --feature-view "${VIEW_ID}" \
    --set-cron "${TEST_CRON}" \
    --dry-run
log_pass "Part B passed: In-engine schedule set dry-run succeeded."

log_info "Part C: Inspecting active schedule with --get (dry-run)..."
"${PYTHON_BIN}" -m tools.feature_store_migration.step4_manage_sync_schedule \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --online-store "${STORE_ID}" \
    --feature-view "${VIEW_ID}" \
    --get \
    --dry-run
log_pass "Part C passed: Schedule inspection dry-run succeeded."

log_info "Part D: Disabling in-engine schedule (dry-run)..."
"${PYTHON_BIN}" -m tools.feature_store_migration.step4_manage_sync_schedule \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --online-store "${STORE_ID}" \
    --feature-view "${VIEW_ID}" \
    --disable \
    --dry-run
log_pass "Part D passed: In-engine schedule disable dry-run succeeded."

log_pass "TEST 2 COMPLETED SUCCESSFULLY: Sync & In-Engine Scheduling Verified."
exit 0
