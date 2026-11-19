#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

set -euo pipefail

# =====================================================================
# AgentOps SDK: Developer Log Export Utility
# Pushes structured LLM stats and case telemetry logs from Cloud Logging
# into BigQuery and/or GCS on demand.
# =====================================================================

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || echo "")}}}}"
if [[ -z "${PROJECT_ID}" ]]; then
    echo "ERROR: GCP Project ID is required. Pass it as arg 1 ($0 <PROJECT_ID>) or set GOOGLE_CLOUD_PROJECT."
    exit 1
fi
REGION="${REGION:-europe-west4}"
DATASET_ID="${DATASET_ID:-agentops_observability}"
BUCKET_NAME="${BUCKET_NAME:-${PROJECT_ID}-agentops-observability-logs}"
HOURS="${HOURS:-24}"
LIMIT="${LIMIT:-500}"
OUTPUT_FILE="${OUTPUT_FILE:-exported_telemetry_logs.jsonl}"

echo "====================================================================="
echo " AgentOps SDK: Exporting Cloud Logs into BigQuery & GCS"
echo " Project:     ${PROJECT_ID}"
echo " Time Window: Last ${HOURS} hour(s)"
echo " Max Entries: ${LIMIT}"
echo " Output File: ${OUTPUT_FILE}"
echo "====================================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
uv run python -m tools.observability.log_router_admin \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --dataset="${DATASET_ID}" \
    --bucket="${BUCKET_NAME}" \
    export \
    --hours="${HOURS}" \
    --limit="${LIMIT}" \
    --output-file="${OUTPUT_FILE}"

echo "Log export completed successfully."
