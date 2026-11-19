#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 2: Per-Case Telemetry Tracking & Composite Export (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
PYTEST_BIN="${REPO_ROOT}/.venv/bin/pytest"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
    PYTEST_BIN="pytest"
fi

export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"
cd "${REPO_ROOT}"

echo "=== [TEST 2] Running Telemetry Unit Suite & Multi-Stage Export Tests ==="
"${PYTEST_BIN}" -v tests/unit/test_telemetry.py

"${PYTHON_BIN}" - << 'PYEOF'
import json
from pathlib import Path
import tempfile
import time

from agentops_sdk import CompositeTelemetryExporter
from agentops_sdk import InMemoryTelemetryExporter
from agentops_sdk import LocalFileTelemetryExporter
from agentops_sdk import UseCaseTracker
from agentops_sdk import trace_use_case

mem_exp = InMemoryTelemetryExporter()
with tempfile.TemporaryDirectory() as tmp:
    jsonl = Path(tmp) / "case_telemetry.jsonl"
    file_exp = LocalFileTelemetryExporter(jsonl, base_dir=tmp)
    comp_exp = CompositeTelemetryExporter([mem_exp, file_exp])

    with UseCaseTracker("case_parts_fulfillment", exporter=comp_exp) as tracker:
        tracker.start_stage("stage_a_validation")
        time.sleep(0.02)
        tracker.end_stage("stage_a_validation")
        tracker.start_stage("stage_b_matching")
        time.sleep(0.02)
        tracker.end_stage("stage_b_matching")
        tracker.add_transactions(4)
        tracker.set_metadata("dealer_id", "DLR-501")

    rec = mem_exp.records[0]
    assert rec.transactions_processed == 4
    assert "stage_a_validation" in rec.stages
    assert rec.total_latency_ms >= 35.0
    assert rec.metadata["dealer_id"] == "DLR-501"
    loaded = json.loads(jsonl.read_text().splitlines()[0])
    assert loaded["use_case_name"] == "case_parts_fulfillment"


@trace_use_case("case_service_orders", business_kpi="service-sla")
def process_service_tickets(tickets: list[str]) -> dict[str, str]:
    return {"count": str(len(tickets)), "status": "ASSIGNED"}


assert process_service_tickets(["T-1", "T-2"])["status"] == "ASSIGNED"
print("[PASS] Multi-stage UseCaseTracker & @trace_use_case verified.")
PYEOF
