#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 3: Developer Cloud Logging Export Utility Validation (v2: Hermetic + Live)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

MODE="${MODE:-hermetic}"
if [[ "${1:-}" == "--offline" ]]; then
    MODE="hermetic"
    shift
elif [[ "${1:-}" == "--live" ]]; then
    MODE="live"
    shift
fi

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-}}}"
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"
export MODE

cd "${REPO_ROOT}"

if [[ "${MODE}" == "hermetic" ]]; then
    HERMETIC_PROJECT="${PROJECT_ID:-example-tenant-01}"
    echo "Running Test 3: Cloud Logging Export Utility (MODE=hermetic, project=${HERMETIC_PROJECT})..."

    # 1. setup --dry-run with --project-id
    DRY_OUT="$("${PYTHON_BIN}" -m tools.observability.log_router_admin \
        setup --project-id="${HERMETIC_PROJECT}" --dry-run)"
    echo "${DRY_OUT}" | grep -q "\[DRY-RUN\] BigQuery Sink"
    echo "${DRY_OUT}" | grep -q "\[DRY-RUN\] GCS Sink"
    echo "${DRY_OUT}" | grep -q "${HERMETIC_PROJECT}"

    # 2. --destination file without --output-file must be rejected (exit != 0)
    if "${PYTHON_BIN}" -m tools.observability.log_router_admin \
        export --project-id="${HERMETIC_PROJECT}" --destination file 2>/dev/null; then
        echo "[FAIL] Expected rejection for --destination file without --output-file."
        exit 1
    fi

    # 3. Local JSONL export via mocked Cloud Logging client
    "${PYTHON_BIN}" - "${HERMETIC_PROJECT}" <<'PYEOF'
import datetime
import json
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tools.observability import log_router_admin

project_id = sys.argv[1]

fake_entry = SimpleNamespace(
    insert_id="ins-001",
    timestamp=datetime.datetime(2026, 11, 22, 0, 0, tzinfo=datetime.UTC),
    severity="INFO",
    log_name=f"projects/{project_id}/logs/agentops-finops",
    payload={
        "event_type": "llm_usage",
        "tenant_id": "tenant-acme",
        "user_id": "analyst@example.com",
        "part_series": "Series-700",
        "unit": "UNIT-001",
    },
    labels={"env": "hermetic"},
    resource=SimpleNamespace(type="generic_task", labels={"location": "europe-west4"}),
    trace=f"projects/{project_id}/traces/trace-obs-001",
    span_id="span-001",
)

mock_logging_client = MagicMock()
mock_logging_client.list_entries.return_value = [fake_entry]

with tempfile.TemporaryDirectory() as tmpdir:
    out_jsonl = os.path.join(tmpdir, "hermetic_export.jsonl")
    with (
        patch("google.cloud.logging.Client", return_value=mock_logging_client),
        patch("google.cloud.bigquery.Client", return_value=MagicMock()),
        patch("google.cloud.storage.Client", return_value=MagicMock()),
    ):
        log_router_admin.main([
            "export",
            "--project-id",
            project_id,
            "--hours",
            "6",
            "--limit",
            "10",
            "--no-bq",
            "--no-gcs",
            "--destination",
            "file",
            "--output-file",
            out_jsonl,
        ])
    assert os.path.isfile(out_jsonl), "Expected JSONL export file"
    with open(out_jsonl, encoding="utf-8") as fh:
        lines = [line.strip() for line in fh if line.strip()]
    assert len(lines) == 1, f"Expected 1 JSONL record, got {len(lines)}"
    record = json.loads(lines[0])
    assert record["payload"]["tenant_id"] == "tenant-acme"
    assert record["payload"]["unit"] == "UNIT-001"
PYEOF

    echo "[PASS] Test 3: Developer Cloud Logging export utility validated (MODE=hermetic)."
    exit 0
fi

# MODE=live
if [[ -z "${PROJECT_ID}" ]]; then
    PROJECT_ID="$(gcloud config get-value project 2>/dev/null || echo "")"
fi
if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "local-offline" ]]; then
    echo "[SKIP] Test 3 (MODE=live) requires a live GCP Project ID for Cloud Logging export."
    exit 0
fi

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
echo "[PASS] Test 3: Developer Cloud Logging export utility validated (MODE=live)."
