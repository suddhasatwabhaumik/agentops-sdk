#!/usr/bin/env bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# TEST 1: Sync/Async AuditLedger, PII/Bearer Scrubbing, BigQuery & InMemory Adapters

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
fi
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}}"
LOCATION="${2:-europe-west4}"

export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"
export MODE

cd "${REPO_ROOT}"
echo "Running Test 1: BigQuery & InMemory AuditLedger (MODE=${MODE})..."

"${PYTHON_BIN}" - "${MODE}" "${PROJECT_ID}" "${LOCATION}" <<'PYEOF'
import asyncio
import os
import stat
import sys
import tempfile
from unittest.mock import MagicMock

from agentops_sdk.audit import (
    AuditLedger,
    BigQueryAuditAdapter,
    InMemoryAuditAdapter,
    LocalFileAuditAdapter,
)
from agentops_sdk.domain import exceptions, models

mode = sys.argv[1]
project_id = sys.argv[2]

# 1. InMemoryAuditAdapter sync & async + PII/Bearer scrubbing on errors
mem_sink = InMemoryAuditAdapter()
ledger = AuditLedger(sink=mem_sink)

sync_entry = models.AuditLogEntry(
    trace_id="trace-audit-001",
    session_id="sess-acme-001",
    user_id="analyst@example.com",
    agent_name="parts-inventory-agent",
    input_query="Check Series-700 stock for UNIT-001",
    generated_sql="SELECT sku, qty FROM enterprise_parts.inventory WHERE unit_id = 'UNIT-001'",
    execution_status="ERROR",
    rows_returned=0,
    error_message=(
        "Auth failed for analyst@example.com phone 555-867-5309 "
        "with Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.secret.sig"
    ),
    latency_ms=12.4,
    safety_sanitization_status="ALLOW",
)
assert ledger.log_entry_sync(sync_entry) is True

async_entry = models.AuditLogEntry(
    trace_id="trace-audit-002",
    session_id="sess-acme-002",
    user_id="operator@example.com",
    agent_name="parts-inventory-agent",
    input_query="Check Series-700 stock for UNIT-002",
    generated_sql="SELECT sku, qty FROM enterprise_parts.inventory WHERE unit_id = 'UNIT-002'",
    execution_status="OK",
    rows_returned=5,
    error_message=None,
    latency_ms=8.1,
    safety_sanitization_status="SANITIZED",
)
assert asyncio.run(ledger.log_entry(async_entry)) is True

entries = mem_sink.entries
assert len(entries) == 2
scrubbed_err = entries[0].error_message or ""
assert "analyst@example.com" not in scrubbed_err, f"Email leaked: {scrubbed_err}"
assert "555-867-5309" not in scrubbed_err, f"Phone leaked: {scrubbed_err}"
assert "secret.sig" not in scrubbed_err, f"Bearer token leaked: {scrubbed_err}"
assert entries[1].execution_status == "OK"

# 2. LocalFileAuditAdapter 0o600 enforcement
with tempfile.TemporaryDirectory() as tmpdir:
    audit_path = os.path.join(tmpdir, "audit.jsonl")
    with LocalFileAuditAdapter(audit_path, base_dir=tmpdir) as file_sink:
        file_ledger = AuditLedger(sink=file_sink)
        assert file_ledger.log_entry_sync(async_entry) is True
    file_mode = stat.S_IMODE(os.stat(audit_path).st_mode)
    assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"

# 3. BigQueryAuditAdapter
if mode == "hermetic":
    mock_bq = MagicMock()
    mock_bq.insert_rows_json.return_value = []
    bq_adapter = BigQueryAuditAdapter(
        project_id="example-tenant-01",
        dataset_id="agentops_governance",
        table_id="audit_ledger",
        client=mock_bq,
        auto_create_table=False,
    )
    bq_ledger = AuditLedger(sink=bq_adapter)
    assert bq_ledger.log_entry_sync(sync_entry) is True
    mock_bq.insert_rows_json.assert_called_once()
    inserted_rows = mock_bq.insert_rows_json.call_args[0][1]
    assert len(inserted_rows) == 1
    assert "analyst@example.com" not in (inserted_rows[0]["error_message"] or "")

    # Verify BigQuery insert errors raise AuditLedgerError
    mock_bq.insert_rows_json.return_value = [{"index": 0, "errors": ["invalid"]}]
    try:
        bq_ledger.log_entry_sync(async_entry)
        raise AssertionError("Expected AuditLedgerError on BQ insert error")
    except exceptions.AuditLedgerError:
        pass
else:
    if not project_id or project_id == "local-offline":
        print("[SKIP] Live BigQuery audit test requires a valid GCP project_id.")
        sys.exit(0)
    with BigQueryAuditAdapter(project_id=project_id) as bq_adapter:
        bq_ledger = AuditLedger(sink=bq_adapter)
        assert bq_ledger.log_entry_sync(async_entry) is True

print("AuditLedger & adapters verified successfully.")
PYEOF

echo "[PASS] Test 1: BigQuery & InMemory AuditLedger validated."
