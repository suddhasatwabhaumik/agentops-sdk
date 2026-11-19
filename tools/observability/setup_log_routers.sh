#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

set -euo pipefail

# =====================================================================
# AgentOps SDK: GCP Log Router Setup for BigQuery & GCS Observability
# =====================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || echo "")}}}}"
if [[ -z "${PROJECT_ID}" ]]; then
    echo "ERROR: GCP Project ID is required. Pass it as arg 1 ($0 <PROJECT_ID>) or set GOOGLE_CLOUD_PROJECT."
    exit 1
fi
REGION="${2:-${GOOGLE_CLOUD_LOCATION:-${GCP_REGION:-${REGION:-europe-west4}}}}"
DATASET_ID="${3:-${DATASET_ID:-agentops_observability}}"
BUCKET_NAME="${4:-${BUCKET_NAME:-${PROJECT_ID}-agentops-observability-logs}}"
BQ_SINK_NAME="${5:-${BQ_SINK_NAME:-agentops-finops-llm-bq-sink}}"
GCS_SINK_NAME="${6:-${GCS_SINK_NAME:-agentops-finops-llm-gcs-sink}}"

LOG_FILTER='logName:"logs/agentops-finops-telemetry" OR logName:"logs/agentops-usecase-telemetry" OR jsonPayload.event_type="finops_llm_telemetry" OR jsonPayload.event_type="use_case_telemetry"'

echo "====================================================================="
echo " AgentOps SDK: Configuring GCP Log Routers for Observability"
echo " Project:  ${PROJECT_ID}"
echo " Region:   ${REGION}"
echo " Dataset:  ${DATASET_ID}"
echo " Bucket:   gs://${BUCKET_NAME}"
echo "====================================================================="

# 1. Ensure BigQuery Analytics Dataset exists
echo "[1/4] Checking BigQuery dataset '${DATASET_ID}'..."
if ! bq show --project_id="${PROJECT_ID}" "${DATASET_ID}" >/dev/null 2>&1; then
    echo "Creating BigQuery dataset '${DATASET_ID}' in location '${REGION}'..."
    if ! bq --location="${REGION}" mk -d \
        --project_id="${PROJECT_ID}" \
        --description="AgentOps Observability & FinOps LLM analytics dataset" \
        "${DATASET_ID}" 2>/dev/null; then
        echo "[ADVISORY] Missing 'bigquery.datasets.create' permission in ${PROJECT_ID}."
    fi
else
    echo "BigQuery dataset '${DATASET_ID}' already exists."
fi

# 2. Ensure GCS Log Archive Bucket exists (hardened defaults)
echo "[2/4] Checking GCS bucket 'gs://${BUCKET_NAME}'..."
if ! gcloud storage buckets describe "gs://${BUCKET_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "Creating GCS bucket 'gs://${BUCKET_NAME}' in location '${REGION}'..."
    if ! gcloud storage buckets create "gs://${BUCKET_NAME}" \
        --project="${PROJECT_ID}" \
        --location="${REGION}" \
        --default-storage-class=STANDARD \
        --uniform-bucket-level-access \
        --public-access-prevention 2>/dev/null; then
        echo "[ADVISORY] Missing storage bucket creation permissions in ${PROJECT_ID}."
    fi
else
    echo "GCS bucket 'gs://${BUCKET_NAME}' already exists."
fi

# 3. Create or Update BigQuery Log Router Sink
echo "[3/4] Configuring BigQuery Log Router Sink '${BQ_SINK_NAME}'..."
BQ_DESTINATION="bigquery.googleapis.com/projects/${PROJECT_ID}/datasets/${DATASET_ID}"

if gcloud logging sinks describe "${BQ_SINK_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "Updating existing BigQuery log sink '${BQ_SINK_NAME}'..."
    gcloud logging sinks update "${BQ_SINK_NAME}" "${BQ_DESTINATION}" \
        --project="${PROJECT_ID}" \
        --log-filter="${LOG_FILTER}" \
        --use-partitioned-tables || echo "[ADVISORY] 'logging.sinks.update' requires 'roles/logging.configWriter'."
else
    echo "Creating new BigQuery log sink '${BQ_SINK_NAME}'..."
    if ! gcloud logging sinks create "${BQ_SINK_NAME}" "${BQ_DESTINATION}" \
        --project="${PROJECT_ID}" \
        --log-filter="${LOG_FILTER}" \
        --use-partitioned-tables 2>/dev/null; then
        echo "[ADVISORY] 'logging.sinks.create' denied on project '${PROJECT_ID}'."
        echo "           Provision Log Router sinks via platform IaC with roles/logging.configWriter."
    fi
fi

if gcloud logging sinks describe "${BQ_SINK_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    BQ_WRITER_IDENTITY="$(gcloud logging sinks describe "${BQ_SINK_NAME}" --project="${PROJECT_ID}" --format='value(writerIdentity)')"
    echo "BigQuery Sink Writer Identity: ${BQ_WRITER_IDENTITY}"
    echo "Granting WRITER access on dataset '${DATASET_ID}' via LogRouterManager..."
    PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
    if [[ ! -x "${PYTHON_BIN}" ]]; then
        PYTHON_BIN="python3"
    fi
    if ! PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}" "${PYTHON_BIN}" - \
        "${PROJECT_ID}" "${REGION}" "${DATASET_ID}" "${BQ_WRITER_IDENTITY}" \
        <<'EOF' 2>/dev/null; then
import sys
from tools.observability import log_router_admin

log_router_admin.LogRouterManager(
    project_id=sys.argv[1],
    region=sys.argv[2],
    dataset_id=sys.argv[3],
)._grant_bigquery_dataset_access(sys.argv[4])
EOF
        echo "[ADVISORY] Grant WRITER/roles/bigquery.dataEditor on ${DATASET_ID} to ${BQ_WRITER_IDENTITY}."
    fi
fi

# 4. Create or Update GCS Log Router Sink
echo "[4/4] Configuring GCS Log Router Sink '${GCS_SINK_NAME}'..."
GCS_DESTINATION="storage.googleapis.com/${BUCKET_NAME}"

if gcloud logging sinks describe "${GCS_SINK_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "Updating existing GCS log sink '${GCS_SINK_NAME}'..."
    gcloud logging sinks update "${GCS_SINK_NAME}" "${GCS_DESTINATION}" \
        --project="${PROJECT_ID}" \
        --log-filter="${LOG_FILTER}" || echo "[ADVISORY] 'logging.sinks.update' requires 'roles/logging.configWriter'."
else
    echo "Creating new GCS log sink '${GCS_SINK_NAME}'..."
    if ! gcloud logging sinks create "${GCS_SINK_NAME}" "${GCS_DESTINATION}" \
        --project="${PROJECT_ID}" \
        --log-filter="${LOG_FILTER}" 2>/dev/null; then
        echo "[ADVISORY] 'logging.sinks.create' denied on project '${PROJECT_ID}'."
        echo "           Provision Log Router sinks via platform IaC with roles/logging.configWriter."
    fi
fi

if gcloud logging sinks describe "${GCS_SINK_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    GCS_WRITER_IDENTITY="$(gcloud logging sinks describe "${GCS_SINK_NAME}" --project="${PROJECT_ID}" --format='value(writerIdentity)')"
    echo "GCS Sink Writer Identity: ${GCS_WRITER_IDENTITY}"
    echo "Granting roles/storage.objectCreator to ${GCS_WRITER_IDENTITY} on 'gs://${BUCKET_NAME}'..."
    gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
        --member="${GCS_WRITER_IDENTITY}" \
        --role="roles/storage.objectCreator" 2>/dev/null || true
fi

echo "====================================================================="
echo " AgentOps SDK: Log Router Configuration Run Complete"
echo " BigQuery Destination: ${BQ_DESTINATION}"
echo " GCS Destination:      ${GCS_DESTINATION}"
echo "====================================================================="
