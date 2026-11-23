# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Tutorial 14: immutable audit trail in BigQuery via BigQueryAuditAdapter."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared import bigquery as bq
from _shared import settings
from _shared import telemetry

cfg = settings.load("14_audit_ledger")
tel = telemetry.start(cfg)

from google.api_core.exceptions import GoogleAPIError
from google.cloud import bigquery

from agentops_sdk import AuditLedger
from agentops_sdk import AuditLogEntry
from agentops_sdk import BigQueryAuditAdapter
from agentops_sdk import bind_execution_scope
from agentops_sdk import trace_agent_flow
from agentops_sdk import validate_sql_query

DATASET = "agentops_governance"
TABLE = "audit_ledger"
STOCK_TABLE = bq.tutorial_table()
USER = "tenant-4711"
SESSION = f"session-{cfg.run_id}"

# AuditLedger defaults to CloudLoggingAuditAdapter; select
# BigQueryAuditAdapter explicitly when durable BigQuery rows are needed.
LEDGER = AuditLedger(
    sink=BigQueryAuditAdapter(
        project_id=cfg.project,
        dataset_id=DATASET,
        table_id=TABLE,
    )
)


async def audited_query(question: str, sql: str) -> int:
    """Validate SQL, run allowed queries on BigQuery, and write audit row."""
    started = time.perf_counter()
    verdict = validate_sql_query(
        sql, dialect="bigquery", allowed_tables=[STOCK_TABLE]
    )
    rows: list[dict] = []
    status = "BLOCKED"
    error = verdict.violation_reason
    if verdict.is_safe:
        try:
            rows = bq.run(sql, max_rows=5)
            status, error = "SUCCESS", None
        except (GoogleAPIError, RuntimeError, ValueError, OSError) as exc:
            status, error = "FAILED", str(exc)[:200]

    await LEDGER.log_entry(
        AuditLogEntry(
            trace_id=telemetry.current_trace_id(),
            session_id=SESSION,
            user_id=USER,
            agent_name="parts_advisor",
            input_query=question,
            generated_sql=sql,
            execution_status=status,
            rows_returned=len(rows),
            error_message=error,
            latency_ms=(time.perf_counter() - started) * 1000,
            safety_sanitization_status="none",
        )
    )
    print(f"   {status}: {len(rows)} rows, audited")
    return len(rows)


def _read_back_rows() -> list[dict[str, object]]:
    query = (
        "SELECT execution_status, rows_returned, generated_sql "
        f"FROM `{cfg.project}.{DATASET}.{TABLE}` "
        "WHERE session_id = @session_id ORDER BY timestamp"
    )
    params = [bigquery.ScalarQueryParameter("session_id", "STRING", SESSION)]
    for _ in range(6):
        rows = bq.run(query, params=params)
        if len(rows) >= 2:
            return rows
        time.sleep(5)
    return []


@trace_agent_flow(
    flow_name="audit_ledger_demo",
    kpi_id="tenant-parts-availability",
    pl_impact_line="aftersales-opex",
)
async def main() -> None:
    """Audit both an allowed query and a SQL-guard blocked query."""
    with bind_execution_scope(session_id=SESSION, user_id=USER):
        print("\n1) allowed query")
        await audited_query(
            "What is in stock?", f"SELECT * FROM `{STOCK_TABLE}` LIMIT 5"
        )
        print("2) disallowed table query")
        await audited_query(
            "Show credit limits",
            f"SELECT * FROM `{cfg.project}.finance.credit_limits`",
        )

    tel.flush()
    print("\nreading audit rows back from BigQuery ...")
    rows = _read_back_rows()
    for row in rows:
        print(f"   {row}")
    if len(rows) < 2:
        sys.exit("Expected 2 audit rows in BigQuery.")


if __name__ == "__main__":
    asyncio.run(main())
