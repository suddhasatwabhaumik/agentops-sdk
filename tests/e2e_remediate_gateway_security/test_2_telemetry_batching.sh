#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

echo "========================================================"
echo "E2E Test 2: Telemetry Batching & FinOps Emission Verification (v1)"
echo "========================================================"

"${PYTHON_BIN}" - << 'PYEOF'
import logging

from agentops_sdk import bind_execution_scope
from agentops_sdk import configure_telemetry
from agentops_sdk import log_llm_metrics
from agentops_sdk import reset_telemetry
from agentops_sdk import trace_agent_flow
from agentops_sdk import trace_llm_call
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

records: list[dict] = []


class _Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        jf = getattr(record, "json_fields", None)
        if isinstance(jf, dict):
            records.append(jf)


root = logging.getLogger()
prev_level = root.level
root.setLevel(logging.INFO)
cap = _Capture()
root.addHandler(cap)

handles = configure_telemetry(
    mode="otlp",
    service_name="e2e-gateway-telemetry",
    collector_url="http://localhost:4317",
)
try:
    assert isinstance(handles.tracer_provider, TracerProvider)
    processors = handles.tracer_provider._active_span_processor._span_processors
    assert any(isinstance(p, BatchSpanProcessor) for p in processors)

    @trace_llm_call(
        kpi_id="gateway-latency",
        pl_impact_line="platform-opex",
        project_name="gateway-sec",
        environment="test",
    )
    def llm_stub() -> dict:
        log_llm_metrics(model_name="gemini-2.5-flash", input_tokens=40, output_tokens=15)
        return {
            "model_name": "gemini-2.5-flash",
            "usage": {"prompt_token_count": 40, "candidates_token_count": 15},
        }

    @trace_agent_flow(flow_name="gateway_flow", kpi_id="gateway-latency")
    def run_flow() -> dict:
        return llm_stub()

    with bind_execution_scope(session_id="gw-sess-1", user_id="gw-user-1"):
        run_flow()

    assert len(records) == 1
    assert records[0]["event_type"] == "finops_llm_telemetry"
    assert records[0]["llm.input_tokens"] == 40
    assert records[0]["llm.output_tokens"] == 15
    print("  => BatchSpanProcessor and flat FinOps log batching verified.")
finally:
    root.removeHandler(cap)
    root.setLevel(prev_level)
    handles.shutdown()
    reset_telemetry()
PYEOF

echo "[PASS] Telemetry batching and OpenTelemetry metrics verified."
