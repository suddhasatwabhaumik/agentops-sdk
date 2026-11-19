#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 3: FinOps Trace-to-Log Correlation & Concurrency Safety (v1)

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
import concurrent.futures
import logging
import os
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from agentops_sdk import bind_execution_scope
from agentops_sdk import trace_agent_flow
from agentops_sdk import trace_llm_call

PROJECT_ID = os.environ["GOOGLE_CLOUD_PROJECT"]
exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)

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

try:
    @trace_llm_call(project_name=PROJECT_ID)
    def step_llm_inference(prompt: str) -> dict:
        return {
            "model_name": "gemini-2.5-flash",
            "usage": {"prompt_token_count": 800, "candidates_token_count": 200},
            "text": f"Summary for: {prompt}",
        }

    @trace_agent_flow(
        agent_role="research_orchestrator",
        flow_name="run_market_research",
        project_name=PROJECT_ID,
    )
    def market_research_flow(topic: str) -> dict:
        return {"status": "SUCCESS", "llm": step_llm_inference(topic)}

    with bind_execution_scope(session_id="sess-corr-1", user_id="researcher-1"):
        res = market_research_flow("GenAI Enterprise Trends 2026")
    assert res["status"] == "SUCCESS"

    spans = exporter.get_finished_spans()
    flow_span = [s for s in spans if s.name == "agent_flow:run_market_research"][0]
    llm_span = [s for s in spans if s.name == "llm_call:step_llm_inference"][0]
    assert llm_span.parent.span_id == flow_span.context.span_id

    log = captured_logs[0]
    assert log["trace_id"] == format(llm_span.context.trace_id, "032x")
    assert log["span_id"] == format(llm_span.context.span_id, "016x")
    assert log["agentops.session.id"] == "sess-corr-1"
    assert log["agentops.user.id"] == "researcher-1"

    captured_logs.clear()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(market_research_flow, f"topic-{idx}") for idx in range(16)
        ]
        for fut in futures:
            assert fut.result()["status"] == "SUCCESS"
    assert len(captured_logs) == 16
    print("[PASS] Test 3: Trace-to-log correlation & thread safety verified.")
finally:
    sdk_logger.removeHandler(handler)
    sdk_logger.setLevel(prev_level)
    provider.shutdown()
PYEOF
