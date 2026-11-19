#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 1: Universal Agent Flow Tracing & Span Hierarchy (v1)

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
import asyncio
import os
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from agentops_sdk import bind_execution_scope
from agentops_sdk import trace_agent_flow

PROJECT_ID = os.environ["GOOGLE_CLOUD_PROJECT"]
exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)


@trace_agent_flow(
    agent_role="lead_architect",
    flow_name="sync_system_design",
    kpi_id="lead-time-reduction",
    pl_impact_line="inventory-opex",
    project_name=PROJECT_ID,
    environment="staging",
)
def sync_design_flow(spec: str) -> str:
    return f"Design for: {spec}"


with bind_execution_scope(session_id="sess-1", user_id="architect-1"):
    assert sync_design_flow("enterprise-architecture") == "Design for: enterprise-architecture"

spans = exporter.get_finished_spans()
assert len(spans) == 1
s1 = spans[0]
assert s1.name == "agent_flow:sync_system_design"
assert s1.attributes["business.kpi_id"] == "lead-time-reduction"
assert s1.attributes["business.pl_impact_line"] == "inventory-opex"
assert s1.attributes["agentops.project.name"] == PROJECT_ID
assert s1.attributes["agentops.environment"] == "staging"
assert s1.attributes["agentops.agent.role"] == "lead_architect"
assert s1.attributes["agentops.session.id"] == "sess-1"
assert s1.attributes["agentops.user.id"] == "architect-1"
assert s1.status.status_code == trace.StatusCode.OK
exporter.clear()


@trace_agent_flow(
    agent_role="async_evaluator",
    flow_name="async_rag_pipeline",
    kpi_id="accuracy-score",
    project_name=PROJECT_ID,
)
async def async_rag_flow(query: str) -> dict[str, str]:
    await asyncio.sleep(0.01)
    return {"status": "retrieved", "query": query}


assert asyncio.run(async_rag_flow("what is RAG?"))["status"] == "retrieved"
s2 = exporter.get_finished_spans()[0]
assert s2.name == "agent_flow:async_rag_pipeline"
assert s2.attributes["agentops.agent.role"] == "async_evaluator"
exporter.clear()


@trace_agent_flow(agent_role="leaf_tool", flow_name="leaf_db_query")
async def leaf_tool_action(data_id: str) -> str:
    await asyncio.sleep(0.01)
    return f"record_{data_id}"


@trace_agent_flow(agent_role="intermediate_planner", flow_name="planner_step")
async def planner_step_action(data_id: str) -> str:
    return f"planned_{await leaf_tool_action(data_id)}"


@trace_agent_flow(agent_role="root_orchestrator", flow_name="root_pipeline")
async def root_orchestrator_flow() -> dict[str, str]:
    return {"result": await planner_step_action("42")}


assert asyncio.run(root_orchestrator_flow())["result"] == "planned_record_42"
spans = exporter.get_finished_spans()
assert len(spans) == 3
leaf, planner, root = spans[0], spans[1], spans[2]
assert leaf.parent.span_id == planner.context.span_id
assert planner.parent.span_id == root.context.span_id
assert root.parent is None
exporter.clear()


@trace_agent_flow(agent_role="faulty_agent", flow_name="failing_operation")
def failing_flow() -> None:
    raise ConnectionResetError("Remote BigQuery connection reset by peer")


try:
    failing_flow()
    raise AssertionError("Should have raised ConnectionResetError")
except ConnectionResetError:
    pass

err_span = exporter.get_finished_spans()[0]
assert err_span.status.status_code == trace.StatusCode.ERROR
assert any(e.name == "exception" for e in err_span.events)
provider.shutdown()
print("[PASS] Test 1: Sync/Async/Nested tracing & v1 attributes verified.")
PYEOF
