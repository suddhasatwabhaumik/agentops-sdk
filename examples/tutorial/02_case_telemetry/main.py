# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Tutorial 02: case telemetry via explicit exporters."""

from __future__ import annotations

from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared import bigquery as bq
from _shared import settings
from _shared import telemetry

cfg = settings.load("02_case_telemetry")
tel = telemetry.start(cfg)

from agentops_sdk import BigQueryTelemetryExporter
from agentops_sdk import CloudLoggingTelemetryExporter
from agentops_sdk import CompositeTelemetryExporter
from agentops_sdk import UseCaseTracker
from agentops_sdk import bind_execution_scope
from agentops_sdk import trace_agent_flow

DATASET = "agentops_observability"
TABLE = "use_case_telemetry"

# UseCaseTracker defaults to CloudLoggingTelemetryExporter only.
# Compose explicit exporters here to write to both Cloud Logging and BigQuery.
EXPORTER = CompositeTelemetryExporter(
    [
        CloudLoggingTelemetryExporter(project_id=cfg.project),
        BigQueryTelemetryExporter(
            project_id=cfg.project,
            dataset_id=DATASET,
            table_id=TABLE,
        ),
    ]
)


@trace_agent_flow(
    flow_name="dealer_order_reconciliation",
    kpi_id="orders-reconciled",
    pl_impact_line="aftersales-opex",
)
def reconcile_orders(dealer_id: str) -> tuple[str, dict[str, object]]:
    """Track per-stage timings and transactions with UseCaseTracker."""
    with UseCaseTracker(
        "dealer_order_reconciliation",
        exporter=EXPORTER,
        business_kpi="orders-reconciled",
        pl_impact_line="aftersales-opex",
    ) as tracker:
        tracker.set_metadata("dealer_id", dealer_id)

        tracker.start_stage("fetch_orders")
        time.sleep(0.12)
        tracker.end_stage("fetch_orders")

        tracker.start_stage("match_invoices")
        time.sleep(0.25)
        tracker.end_stage("match_invoices")

        tracker.start_stage("write_report")
        time.sleep(0.05)
        tracker.end_stage("write_report")

        tracker.add_transactions(42)

    if tracker.record is None:
        raise RuntimeError("UseCaseTracker did not emit a telemetry record.")
    return tracker.record.case_id, {
        "transactions_processed": tracker.record.transactions_processed,
        "dealer_id": dealer_id,
    }


def _poll_bigquery_row(case_id: str) -> dict | None:
    query = (
        "SELECT use_case_name, transactions_processed, "
        "total_latency_ms, trace_id, success "
        f"FROM `{cfg.project}.{DATASET}.{TABLE}` "
        f"WHERE case_id = '{case_id}'"
    )
    for _ in range(6):
        rows = bq.run(query)
        if rows:
            return rows[0]
        time.sleep(5)
    return None


def main() -> None:
    """Run order reconciliation and confirm the record landed in BigQuery."""
    with bind_execution_scope(session_id=cfg.run_id, user_id="dealer-4711"):
        case_id, summary = reconcile_orders("dealer-4711")
    print(f"\nuse case done: {summary}, case_id={case_id}")
    tel.flush()

    print("reading the record back from BigQuery ...")
    row = _poll_bigquery_row(case_id)
    print(f"BigQuery row: {row or 'NOT FOUND'}")
    if row is None:
        sys.exit(
            "Record did not land in BigQuery (check roles/bigquery.dataEditor)."
        )


if __name__ == "__main__":
    main()
