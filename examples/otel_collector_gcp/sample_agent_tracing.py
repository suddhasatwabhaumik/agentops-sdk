# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""OTLP tracing and FinOps metrics example exporting to a local collector."""

import os
import time

from agentops_sdk import bind_execution_scope
from agentops_sdk import configure_telemetry
from agentops_sdk import log_llm_metrics
from agentops_sdk import reset_telemetry
from agentops_sdk import trace_agent_flow
from agentops_sdk import trace_agent_span
from agentops_sdk import trace_llm_call

COLLECTOR_URL = os.environ.get("OTEL_COLLECTOR_URL", "http://localhost:4317")
SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "inventory-advisor-agent")


@trace_llm_call(
    model_name_override="gemini-2.5-flash",
    kpi_id="parts_stockout_reduction",
    pl_impact_line="COGS_Savings",
    project_name="smart-inventory",
    environment="staging",
)
def _draft_replenishment_plan(part_id: str) -> dict[str, object]:
    """Simulate an instrumented LLM reasoning call."""
    time.sleep(0.05)
    log_llm_metrics(
        model_name="gemini-2.5-flash",
        input_tokens=420,
        output_tokens=135,
        cached_tokens=64,
    )
    return {"part_id": part_id, "status": "SUCCESS", "reorder_units": 450}


@trace_agent_flow(
    flow_name="replenishment_advisor",
    agent_role="planner",
    kpi_id="parts_stockout_reduction",
    pl_impact_line="COGS_Savings",
    project_name="smart-inventory",
    environment="staging",
)
@trace_agent_span(agent_name="replenishment_advisor")
def advise_inventory(part_id: str = "ITEM-7741") -> dict[str, object]:
    """Execute replenishment reasoning inside traced spans."""
    return _draft_replenishment_plan(part_id)


def main() -> None:
    """Configure OTLP telemetry, run traced agent flow, and flush."""
    handles = configure_telemetry(
        mode="otlp",
        service_name=SERVICE_NAME,
        collector_url=COLLECTOR_URL,
    )
    try:
        with bind_execution_scope(
            session_id="sess-replenish-001",
            user_id="planner@example.com",
        ):
            result = advise_inventory()
        print("Result:", result)
        print(f"Telemetry flushed to {COLLECTOR_URL}")
    finally:
        handles.shutdown()
        reset_telemetry()


if __name__ == "__main__":
    main()
