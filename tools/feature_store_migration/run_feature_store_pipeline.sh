#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# ==============================================================================
# VERTEX AI FEATURE ONLINE STORE (V2) - PIPELINE SETUP & SYNC ORCHESTRATOR
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO] $(date +'%Y-%m-%d %H:%M:%S')${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS] $(date +'%Y-%m-%d %H:%M:%S')${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN] $(date +'%Y-%m-%d %H:%M:%S')${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR] $(date +'%Y-%m-%d %H:%M:%S')${NC} $1" >&2
}

if [[ -f "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
elif [[ -f "${REPO_ROOT}/venv/bin/python" ]]; then
    PYTHON_BIN="${REPO_ROOT}/venv/bin/python"
else
    PYTHON_BIN="$(command -v python3 || echo "python3")"
fi

export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

PROJECT_ID=""
REGION="${GOOGLE_CLOUD_LOCATION:-${GCP_REGION:-europe-west4}}"
DATASET_ID="agent_feature_store"
TABLE_ID="customer_features"
ONLINE_STORE_ID="agent_online_store"
FEATURE_VIEW_ID="customer_features_view"
CRON_SCHEDULE="0 */4 * * *"
SKIP_DUMMY_DATA=false
SKIP_VERIFICATION=false
NUM_RECORDS=50
ENTITY_IDS=()
WAIT_FLAG=""
DRY_RUN_FLAG=""

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Vertex AI Feature Online Store (v2) End-to-End Orchestration Pipeline.

Options:
  --project <ID>          GCP Project ID (default: active gcloud project)
  --region <REGION>       GCP Region (default: europe-west4)
  --dataset <DATASET>     BigQuery Dataset ID (default: agent_feature_store)
  --table <TABLE>         BigQuery Table ID (default: customer_features)
  --online-store <ID>     Feature Online Store ID (default: agent_online_store)
  --feature-view <ID>     Feature View ID (default: customer_features_view)
  --cron-schedule <CRON>  In-engine sync schedule (default: '0 */4 * * *', or 'none')
  --skip-dummy-data       Skip Step 1 BigQuery test data ingestion
  --skip-verification     Skip Step 5 online lookup verification
  --entity-ids <ID ...>   Custom entity IDs to verify in Step 5
  --num-records <N>       Number of synthetic records to generate (default: 50)
  --wait                  Wait for provisioning and sync completion
  --dry-run               Run in simulation / dry-run mode (no GCP mutations)
  -h, --help              Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project)
            PROJECT_ID="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --dataset)
            DATASET_ID="$2"
            shift 2
            ;;
        --table)
            TABLE_ID="$2"
            shift 2
            ;;
        --online-store)
            ONLINE_STORE_ID="$2"
            shift 2
            ;;
        --feature-view)
            FEATURE_VIEW_ID="$2"
            shift 2
            ;;
        --cron-schedule)
            CRON_SCHEDULE="$2"
            shift 2
            ;;
        --skip-dummy-data)
            SKIP_DUMMY_DATA=true
            shift
            ;;
        --skip-verification)
            SKIP_VERIFICATION=true
            shift
            ;;
        --num-records)
            NUM_RECORDS="$2"
            shift 2
            ;;
        --entity-ids)
            shift
            ENTITY_IDS=()
            while [[ $# -gt 0 && ! "$1" =~ ^-- ]]; do
                ENTITY_IDS+=("$1")
                shift
            done
            ;;
        --wait)
            WAIT_FLAG="--wait"
            shift
            ;;
        --dry-run)
            DRY_RUN_FLAG="--dry-run"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

if [[ -z "${PROJECT_ID}" ]]; then
    if command -v gcloud &>/dev/null; then
        PROJECT_ID="$(gcloud config get-value project 2>/dev/null || echo "")"
    fi
    if [[ -z "${PROJECT_ID}" ]]; then
        if [[ -n "${DRY_RUN_FLAG}" ]]; then
            PROJECT_ID="dry-run-project"
        else
            log_error "GCP Project ID is required. Pass --project <ID> or set GOOGLE_CLOUD_PROJECT."
            exit 1
        fi
    fi
fi

echo -e "${CYAN}=================================================================${NC}"
echo -e "${CYAN}  VERTEX AI FEATURE ONLINE STORE (V2) SYNCHRONIZATION PIPELINE  ${NC}"
echo -e "${CYAN}=================================================================${NC}"
log_info "Target Project ID:     ${PROJECT_ID}"
log_info "Target Region:         ${REGION}"
log_info "BigQuery Table:        ${PROJECT_ID}.${DATASET_ID}.${TABLE_ID}"
log_info "Feature Online Store:  ${ONLINE_STORE_ID}"
log_info "Feature View:          ${FEATURE_VIEW_ID}"
log_info "In-Engine Schedule:    ${CRON_SCHEDULE}"
log_info "Python Runtime:        ${PYTHON_BIN}"
if [[ -n "${DRY_RUN_FLAG}" ]]; then
    log_warn "Execution Mode:        DRY-RUN / SIMULATION (No GCP charges)"
else
    log_info "Execution Mode:        LIVE GCP CLOUD"
fi
echo -e "${CYAN}-----------------------------------------------------------------${NC}"

log_info "Running preflight environment checks..."
"${PYTHON_BIN}" -c "import google.cloud.aiplatform_v1; import google.cloud.bigquery; import agentops_sdk.feature_store" || {
    log_error "Missing required Python dependencies in runtime ${PYTHON_BIN}."
    exit 1
}
log_success "Preflight checks passed."

# Step 1: Generate & Ingest BigQuery Data
if [[ "${SKIP_DUMMY_DATA}" = false ]]; then
    log_info "[STEP 1/5] Ingesting test feature data into BigQuery (${DATASET_ID}.${TABLE_ID})..."
    "${PYTHON_BIN}" "${SCRIPT_DIR}/step1_seed_dummy_data.py" \
        --project "${PROJECT_ID}" \
        --region "${REGION}" \
        --dataset "${DATASET_ID}" \
        --table "${TABLE_ID}" \
        --num-records "${NUM_RECORDS}" \
        ${DRY_RUN_FLAG}
    log_success "Step 1 completed: BigQuery data ready."
else
    log_info "[STEP 1/5] Skipping dummy data ingestion as requested."
fi

# Step 2: Setup Feature Online Store & Feature View
log_info "[STEP 2/5] Provisioning Feature Online Store and Feature View in Vertex AI..."
SETUP_CRON_ARG=()
if [[ -n "${CRON_SCHEDULE}" && "${CRON_SCHEDULE}" != "none" ]]; then
    SETUP_CRON_ARG=(--sync-cron "${CRON_SCHEDULE}")
fi

"${PYTHON_BIN}" "${SCRIPT_DIR}/step2_setup_feature_store.py" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --dataset "${DATASET_ID}" \
    --table "${TABLE_ID}" \
    --online-store "${ONLINE_STORE_ID}" \
    --feature-view "${FEATURE_VIEW_ID}" \
    ${SETUP_CRON_ARG[@]+"${SETUP_CRON_ARG[@]}"} \
    ${WAIT_FLAG} \
    ${DRY_RUN_FLAG}
log_success "Step 2 completed: Vertex AI Online Store and Feature View active."

# Step 3: Trigger Ingestion Sync
log_info "[STEP 3/5] Triggering BigQuery -> Online Store Ingestion Sync..."
"${PYTHON_BIN}" "${SCRIPT_DIR}/step3_trigger_sync.py" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --online-store "${ONLINE_STORE_ID}" \
    --feature-view "${FEATURE_VIEW_ID}" \
    ${WAIT_FLAG} \
    ${DRY_RUN_FLAG}
log_success "Step 3 completed: Sync job triggered."

# Step 4: Manage In-Engine Zero-Compute Schedule
log_info "[STEP 4/5] Configuring In-Engine Managed Sync Schedule (Zero Compute)..."
if [[ "${CRON_SCHEDULE}" == "none" ]]; then
    "${PYTHON_BIN}" "${SCRIPT_DIR}/step4_manage_sync_schedule.py" \
        --project "${PROJECT_ID}" \
        --region "${REGION}" \
        --online-store "${ONLINE_STORE_ID}" \
        --feature-view "${FEATURE_VIEW_ID}" \
        --disable \
        ${DRY_RUN_FLAG}
else
    "${PYTHON_BIN}" "${SCRIPT_DIR}/step4_manage_sync_schedule.py" \
        --project "${PROJECT_ID}" \
        --region "${REGION}" \
        --online-store "${ONLINE_STORE_ID}" \
        --feature-view "${FEATURE_VIEW_ID}" \
        --set-cron "${CRON_SCHEDULE}" \
        ${DRY_RUN_FLAG}
fi
log_success "Step 4 completed: In-engine sync schedule configured."

# Step 5: Verification & In-Memory / Online Lookup
if [[ "${SKIP_VERIFICATION}" = false ]]; then
    log_info "[STEP 5/5] Verifying Feature Online Store lookup..."
    VERIFY_ENTITY_ARGS=()
    if [[ ${#ENTITY_IDS[@]} -gt 0 ]]; then
        VERIFY_ENTITY_ARGS=(--entity-ids "${ENTITY_IDS[@]}")
    fi
    "${PYTHON_BIN}" "${SCRIPT_DIR}/step5_verify_online_lookup.py" \
        --project "${PROJECT_ID}" \
        --region "${REGION}" \
        --online-store "${ONLINE_STORE_ID}" \
        --feature-view "${FEATURE_VIEW_ID}" \
        ${VERIFY_ENTITY_ARGS[@]+"${VERIFY_ENTITY_ARGS[@]}"} \
        ${DRY_RUN_FLAG}
    log_success "Step 5 completed: Feature Store online lookup verified."
else
    log_info "[STEP 5/5] Skipping online lookup verification as requested."
fi

echo -e "${CYAN}=================================================================${NC}"
log_success "Pipeline execution complete! Vertex AI Feature Online Store is live."
echo -e "${CYAN}=================================================================${NC}"
