# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Tutorial 01: trace an agent flow and record FinOps LLM telemetry."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared import gemini
from _shared import settings
from _shared import telemetry

cfg = settings.load("01_tracing_and_finops")
tel = telemetry.start(cfg)

from agentops_sdk import bind_execution_scope
from agentops_sdk import log_llm_metrics
from agentops_sdk import trace_agent_flow
from agentops_sdk import trace_agent_span
from agentops_sdk import trace_llm_call

KPI_ID = "tenant-parts-availability"
PL_LINE = "aftersales-opex"
TRACE_IDS: list[str] = []


@trace_agent_span(agent_name="stock_lookup")
def lookup_stock(part_number: str) -> dict[str, object]:
    """Tool call traced as a child span of the agent flow."""
    return {
        "part_number": part_number,
        "units_available": 7,
        "warehouse": "Plant-EU-01",
    }


@trace_llm_call(
    kpi_id=KPI_ID,
    pl_impact_line=PL_LINE,
    project_name=cfg.project,
    environment=cfg.environment,
)
def ask_gemini(prompt: str):
    """Return raw GenerateContentResponse so @trace_llm_call logs tokens."""
    return gemini.generate(
        prompt,
        system=(
            "You are an enterprise parts advisor supporting a "
            "Google Customer. Answer the tenant in two short sentences."
        ),
    )


@trace_agent_flow(
    flow_name="tenant_parts_advisor",
    agent_role="orchestrator",
    kpi_id=KPI_ID,
    pl_impact_line=PL_LINE,
    project_name=cfg.project,
    environment=cfg.environment,
)
def tenant_parts_advisor(question: str, part_number: str) -> str:
    """Execute stock lookup followed by Gemini answer generation."""
    TRACE_IDS.append(telemetry.current_trace_id())
    stock = lookup_stock(part_number)
    response = ask_gemini(f"Tenant question: {question}\nStock report: {stock}")

    usage = response.usage_metadata
    log_llm_metrics(
        model_name=response.model_version or gemini.model_name(),
        input_tokens=usage.prompt_token_count or 0,
        output_tokens=usage.candidates_token_count or 0,
    )
    return response.text


def main() -> None:
    """Run traced tenant advisor flow and confirm trace lands in Tempo."""
    with bind_execution_scope(session_id=cfg.run_id, user_id="tenant-4711"):
        answer = tenant_parts_advisor(
            "Do we have oil filters for a Series-700 in stock, "
            "and should I order more?",
            part_number="PART-1001",
        )
    print(f"\nGemini answered:\n  {answer.strip()}\n")
    tel.flush()

    trace_id = TRACE_IDS[0]
    landed = tel.trace_landed(trace_id)
    print(f"trace_id={trace_id} arrived in Tempo: {landed}")
    print(
        f'  Loki query: {{service_name="{cfg.service_name}"}} |= "{trace_id}"'
    )
    if not landed:
        sys.exit(
            "The trace did not reach Tempo. "
            "Check: bash examples/tutorial/observability/stack.sh status"
        )


if __name__ == "__main__":
    main()
