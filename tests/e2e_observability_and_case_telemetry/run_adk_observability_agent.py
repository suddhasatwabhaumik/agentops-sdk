#!/usr/bin/env python3
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""E2E Verification Agent: Case Telemetry Parts & Safety Advisory (v1)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from typing import Any

from agentops_sdk import BatchedGCSTelemetryExporter
from agentops_sdk import BigQueryTelemetryExporter
from agentops_sdk import CloudLoggingTelemetryExporter
from agentops_sdk import CompositeTelemetryExporter
from agentops_sdk import InMemoryTelemetryExporter
from agentops_sdk import LocalFileTelemetryExporter
from agentops_sdk import LocalRuleSafetyScanner
from agentops_sdk import SecurityGuard
from agentops_sdk import UseCaseTracker
from agentops_sdk import bind_execution_scope
from agentops_sdk import configure_telemetry
from agentops_sdk import reset_telemetry
from agentops_sdk import trace_llm_call
from agentops_sdk import validate_sql_query

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("adk_observability_agent")


@trace_llm_call(
    kpi_id="parts-resolution-time",
    pl_impact_line="dealer-operations-opex",
)
def simulate_gemini_parts_recommendation(
    prompt: str, context_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Simulate Gemini synthesis with FinOps token telemetry."""
    del prompt, context_rows
    time.sleep(0.05)
    return {
        "model_name": "gemini-2.5-flash",
        "usage": {"prompt_token_count": 142, "candidates_token_count": 68},
        "finish_reason": "STOP",
        "text": (
            "SKU 'PART-HYD-9902' is in stock at Central Depot "
            "(14 units available). Recommended order quantity: 2."
        ),
    }


def _check_safety_and_sql(
    tracker: UseCaseTracker, equipment_model: str, user_query: str
) -> bool:
    tracker.start_stage("stage_a_safety_and_intent")
    guard = SecurityGuard(scanner=LocalRuleSafetyScanner())
    safety_res = guard.check_input(user_query)
    if not safety_res.allowed:
        tracker.set_metadata("blocked_reason", "SAFETY_VIOLATION")
        tracker.end_stage("stage_a_safety_and_intent")
        return False
    tracker.end_stage("stage_a_safety_and_intent")

    tracker.start_stage("stage_b_sql_ast_guard")
    candidate_sql = (
        "SELECT part_id, sku, description, warehouse_id, stock_quantity "
        "FROM parts_catalog WHERE equipment_model = @equipment_model "
        "AND stock_quantity > 0"
    )
    sql_res = validate_sql_query(
        candidate_sql, allowed_tables=["parts_catalog"]
    )
    tracker.end_stage("stage_b_sql_ast_guard")
    if not sql_res.is_safe:
        tracker.set_metadata("blocked_reason", str(sql_res.violation_reason))
        return False
    return True


_INVENTORY_FIXTURE = [
    {"part_id": 101, "sku": "PART-HYD-9902", "stock": 14},
    {"part_id": 102, "sku": "PART-FLT-4401", "stock": 8},
    {"part_id": 103, "sku": "PART-VAL-1120", "stock": 3},
]


def execute_case_parts_advisor_workflow(
    dealer_id: str,
    equipment_model: str,
    user_query: str,
    exporter: Any = None,
) -> dict[str, Any]:
    """Execute multi-stage Case Telemetry workflow with UseCaseTracker."""
    with (
        bind_execution_scope(session_id=f"sess-{dealer_id}", user_id=dealer_id),
        UseCaseTracker(
            use_case_name="case_parts_advisor",
            business_kpi="parts-resolution-time",
            pl_impact_line="dealer-operations-opex",
            exporter=exporter,
        ) as tracker,
    ):
        tracker.set_metadata("equipment_model", equipment_model)
        tracker.set_metadata("dealer_id", dealer_id)
        if not _check_safety_and_sql(tracker, equipment_model, user_query):
            return {"status": "BLOCKED", "tracker": tracker}
        tracker.start_stage("stage_c_inventory_lookup")
        inventory_items = list(_INVENTORY_FIXTURE)
        time.sleep(0.02)
        tracker.add_transactions(len(inventory_items))
        tracker.end_stage("stage_c_inventory_lookup")
        tracker.start_stage("stage_d_llm_synthesis")
        llm_response = simulate_gemini_parts_recommendation(
            user_query, inventory_items
        )
        tracker.end_stage("stage_d_llm_synthesis")

    return {
        "status": "SUCCESS",
        "dealer_id": dealer_id,
        "equipment_model": equipment_model,
        "parts_found": len(inventory_items),
        "recommendation": llm_response["text"],
        "tracker": tracker,
    }


def _build_exporter(
    args: argparse.Namespace,
) -> CompositeTelemetryExporter:
    exporters: list[Any] = [
        InMemoryTelemetryExporter(),
        LocalFileTelemetryExporter("e2e_case_telemetry.jsonl"),
    ]
    if not args.local_only:
        exporters.extend(
            [
                CloudLoggingTelemetryExporter(project_id=args.project),
                BigQueryTelemetryExporter(
                    project_id=args.project, dataset_id=args.dataset
                ),
            ]
        )
        if args.bucket:
            exporters.append(
                BatchedGCSTelemetryExporter(
                    bucket_name=args.bucket, project_id=args.project
                )
            )
    return CompositeTelemetryExporter(exporters)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        default=os.getenv("GOOGLE_CLOUD_PROJECT", ""),
    )
    parser.add_argument("--dataset", default="agentops_observability")
    parser.add_argument("--bucket", default=None)
    parser.add_argument("--local-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    """Execute Case Telemetry workflow and print recorded case telemetry."""
    args = _parse_args()
    configure_telemetry(mode="off", service_name="adk-case-observability")
    try:
        exporter = _build_exporter(args)
        result = execute_case_parts_advisor_workflow(
            dealer_id="DEALER-MIDWEST-042",
            equipment_model="Series-900",
            user_query=(
                "Customer needs hydraulic control valves for Series-900."
            ),
            exporter=exporter,
        )
        rec = result["tracker"].record
        assert rec is not None
        print(
            f"Recorded Case Telemetry | case_id={rec.case_id} "
            f"txns={rec.transactions_processed} "
            f"stages={json.dumps(rec.stages)}"
        )
    finally:
        reset_telemetry()


if __name__ == "__main__":
    main()
