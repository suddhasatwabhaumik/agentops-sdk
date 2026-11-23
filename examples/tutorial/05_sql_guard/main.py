# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Tutorial 05: AST-based SQL governance before BigQuery execution."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared import bigquery as bq
from _shared import gemini
from _shared import settings
from _shared import telemetry

cfg = settings.load("05_sql_guard")
tel = telemetry.start(cfg)

from agentops_sdk import trace_agent_flow
from agentops_sdk import trace_llm_call
from agentops_sdk import validate_sql_query
from agentops_sdk import validate_sql_query_decorator
from agentops_sdk.domain.exceptions import SQLGovernanceError

TABLE = bq.tutorial_table()
COLUMNS = bq.columns(TABLE)
ALLOWED = [TABLE, f"{TABLE.rsplit('.', 1)[0]}.*"]
TRACE_IDS: list[str] = []


@trace_llm_call(
    kpi_id="tenant-parts-availability",
    pl_impact_line="aftersales-opex",
)
def write_sql(question: str):
    """Generate a candidate BigQuery SQL statement with Gemini."""
    return gemini.generate(
        f"Table: `{TABLE}` (columns: {', '.join(COLUMNS)}).\n"
        f"Question: {question}\n"
        "Return ONLY one BigQuery Standard SQL SELECT statement, no markdown.",
        system="You write safe, read-only BigQuery SQL.",
    )


@validate_sql_query_decorator(
    allowed_tables=ALLOWED,
    dialect="bigquery",
    sql_params=["sql"],
)
def run_query(sql: str) -> list[dict]:
    """Execute SQL only after @validate_sql_query_decorator approves it."""
    return bq.run(sql, max_rows=5)


def clean(sql: str) -> str:
    """Strip markdown code fences if present."""
    return sql.strip().strip("`").removeprefix("sql").strip()


@trace_agent_flow(
    flow_name="sql_guard_demo",
    kpi_id="tenant-parts-availability",
    pl_impact_line="aftersales-opex",
)
def main() -> None:
    """Test valid, unauthorized-table, and stacked-statement SQL."""
    TRACE_IDS.append(telemetry.current_trace_id())

    sql = clean(
        write_sql(f"Count rows per {COLUMNS[1]}, highest first, top 5.").text
    )
    print(f"\n1) Gemini wrote:\n   {sql}")
    verdict = validate_sql_query(
        sql, dialect="bigquery", allowed_tables=ALLOWED
    )
    print(
        f"   guard: is_safe={verdict.is_safe} "
        f"tables={verdict.referenced_tables} reason={verdict.violation_reason}"
    )
    if verdict.is_safe:
        rows = run_query(sql=sql)
        first = rows[0] if rows else None
        print(f"   BigQuery returned {len(rows)} rows, first: {first}")

    hostile = f"SELECT * FROM `{cfg.project}.finance.credit_limits` LIMIT 5"
    print(f"\n2) hostile SQL:\n   {hostile}")
    try:
        run_query(sql=hostile)
        sys.exit("Guard allowed a disallowed table through.")
    except SQLGovernanceError as exc:
        print(f"   blocked before BigQuery: {type(exc).__name__}: {exc}")

    sneaky = f"SELECT * FROM `{TABLE}`; DROP TABLE `{TABLE}`"
    verdict = validate_sql_query(
        sneaky, dialect="bigquery", allowed_tables=ALLOWED
    )
    print(
        f"\n3) stacked statement -> is_safe={verdict.is_safe} "
        f"reason={verdict.violation_reason}"
    )


if __name__ == "__main__":
    try:
        main()
    finally:
        tel.flush()
