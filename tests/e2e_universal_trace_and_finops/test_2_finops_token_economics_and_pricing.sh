#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 2: FinOps Token Economics & Flat JSON Logging (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}}"
export GOOGLE_CLOUD_PROJECT="${PROJECT_ID}"
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

"${PYTHON_BIN}" - << 'PYEOF'
import logging
import os
import time

from agentops_sdk import configure_telemetry
from agentops_sdk import reset_telemetry
from agentops_sdk import trace_llm_call

PROJECT_ID = os.environ["GOOGLE_CLOUD_PROJECT"]
captured_logs: list[dict] = []


class MemoryHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        jf = getattr(record, "json_fields", None)
        if isinstance(jf, dict):
            captured_logs.append(jf)


sdk_logger = logging.getLogger("agentops_sdk")
prev_level = sdk_logger.level
handler = MemoryHandler()
handler.setLevel(logging.INFO)
sdk_logger.addHandler(handler)
sdk_logger.setLevel(logging.INFO)
configure_telemetry(mode="off", service_name="e2e-finops-tokens")

try:
    @trace_llm_call(
        kpi_id="customer-churn-reduction",
        pl_impact_line="sales-retention",
        project_name=PROJECT_ID,
        model_name_override="gemini-2.5-flash",
    )
    def call_gemini_flash(prompt: str) -> dict:
        del prompt
        time.sleep(0.01)
        return {
            "model_name": "gemini-2.5-flash",
            "usage": {"prompt_token_count": 1000, "candidates_token_count": 500},
            "finish_reason": "STOP",
            "text": "Flash analytical response",
        }

    call_gemini_flash("Analyze churn")
    assert len(captured_logs) == 1
    log1 = captured_logs[-1]
    assert log1["event_type"] == "finops_llm_telemetry"
    assert log1["llm.model_name"] == "gemini-2.5-flash"
    assert log1["llm.input_tokens"] == 1000
    assert log1["llm.output_tokens"] == 500
    assert log1["llm.status"] == "OK"
    assert log1["business.kpi_id"] == "customer-churn-reduction"

    @trace_llm_call(model_name_override="gemini-2.5-flash", project_name=PROJECT_ID)
    def call_cached_gemini() -> dict:
        return {
            "model_name": "gemini-2.5-flash",
            "usage": {
                "prompt_token_count": 10000,
                "cached_content_token_count": 8000,
                "candidates_token_count": 500,
            },
        }

    call_cached_gemini()
    log2 = captured_logs[-1]
    assert log2["llm.cached_tokens"] == 8000
    assert log2["llm.input_tokens"] == 10000

    @trace_llm_call(model_name_override="gemini-2.5-pro", project_name=PROJECT_ID)
    def call_failing_llm() -> None:
        raise TimeoutError("Vertex AI API endpoint timed out after 30000ms")

    try:
        call_failing_llm()
        raise AssertionError("Should have raised TimeoutError")
    except TimeoutError:
        pass

    log_err = captured_logs[-1]
    assert log_err["llm.status"] == "ERROR"
    assert "timed out after 30000ms" in log_err["error_message"]
    print("[PASS] Test 2: FinOps flat token economics & error emission verified.")
finally:
    sdk_logger.removeHandler(handler)
    sdk_logger.setLevel(prev_level)
    reset_telemetry()
PYEOF
