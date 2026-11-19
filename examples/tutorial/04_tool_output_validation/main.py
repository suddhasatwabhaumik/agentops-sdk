# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Tutorial 04: schema and semantic validation via @validate_tool_output."""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared import gemini
from _shared import settings
from _shared import telemetry

cfg = settings.load("04_tool_output_validation")
tel = telemetry.start(cfg)

from pydantic import BaseModel
from pydantic import Field

from agentops_sdk import ToolValidationError
from agentops_sdk import trace_agent_flow
from agentops_sdk import validate_tool_output

TRACE_IDS: list[str] = []


class StockReport(BaseModel):
    """Schema contract enforced on tool and LLM structured outputs."""

    part_number: str = Field(min_length=5)
    units_available: int = Field(ge=0)
    warehouse: str


attempts: list[int] = []


@validate_tool_output(schema_model=StockReport, max_retries=3)
def supplier_stock_api(part_number: str) -> dict[str, object]:
    """Return invalid units on attempt 1, valid payload on attempt 2."""
    attempts.append(1)
    if len(attempts) == 1:
        return {
            "part_number": part_number,
            "units_available": -1,
            "warehouse": "?",
        }
    return {
        "part_number": part_number,
        "units_available": 7,
        "warehouse": "Plant-EU-01",
    }


def must_mention_warehouse(report: StockReport | dict[str, object]) -> bool:
    """Assertion callback: return True when valid, False otherwise."""
    warehouse = (
        report.warehouse
        if isinstance(report, StockReport)
        else str(report.get("warehouse", ""))
    )
    return warehouse.strip().lower() not in ("", "unknown", "?")


@validate_tool_output(
    schema_model=StockReport,
    assertion_callback=must_mention_warehouse,
    max_retries=2,
)
def ask_gemini_for_report(question: str) -> dict[str, object]:
    """Validate structured JSON output from Gemini."""
    response = gemini.generate(
        question,
        system=(
            "You are an enterprise parts advisor supporting a "
            "Google Customer. Reply ONLY with JSON."
        ),
        response_schema=StockReport,
    )
    return json.loads(response.text)


@validate_tool_output(schema_model=StockReport, max_retries=2)
def always_broken_tool() -> dict[str, object]:
    """Always violate StockReport to demonstrate ToolValidationError."""
    return {"part_number": "x", "units_available": -5, "warehouse": ""}


@trace_agent_flow(
    flow_name="tool_validation_demo",
    kpi_id="data-quality",
    pl_impact_line="aftersales-opex",
)
def run() -> None:
    """Run retry recovery, LLM schema validation, and exhaustion cases."""
    TRACE_IDS.append(telemetry.current_trace_id())

    report = supplier_stock_api("PART-1001")
    print(f"\n1) flaky tool -> accepted after {len(attempts)} tries: {report}")

    llm_report = ask_gemini_for_report(
        "Part PART-1001 (oil filter) has 7 units in Plant-EU-01. "
        "Return part_number, units_available and warehouse."
    )
    print(f"2) Gemini structured output validated: {llm_report}")

    try:
        always_broken_tool()
    except ToolValidationError as exc:
        print(
            f"3) retries exhausted -> ToolValidationError: "
            f"{exc.last_error[:100]}"
        )


def main() -> None:
    """Execute validation demo and verify trace arrived in Tempo."""
    run()
    tel.flush()
    trace_id = TRACE_IDS[0]
    print(f"\ntrace_id={trace_id} in Tempo: {tel.trace_landed(trace_id)}")


if __name__ == "__main__":
    main()
