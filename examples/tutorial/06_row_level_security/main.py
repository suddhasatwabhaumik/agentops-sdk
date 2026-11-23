# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Tutorial 06: deterministic row-level security via SQL AST rewriting."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared import bigquery as bq
from _shared import settings
from _shared import telemetry

cfg = settings.load("06_row_level_security")
tel = telemetry.start(cfg)

from opentelemetry import trace

from agentops_sdk import apply_row_level_policy
from agentops_sdk import bind_execution_scope
from agentops_sdk import trace_agent_span

TABLE = bq.tutorial_table()
BASE_SQL = (
    f"SELECT country, COUNT(*) AS rows_seen FROM `{TABLE}` "
    "GROUP BY country ORDER BY country"
)

USERS = {
    "tenant-de-4711": {"region": "Germany"},
    "tenant-br-0815": {"region": "Brazil"},
    "ops-admin": {"role": "admin"},
}


@trace_agent_span(agent_name="secure_query")
def secure_query(user_context: dict[str, str]) -> list[dict]:
    """Rewrite BASE_SQL with caller's country predicate before querying BQ."""
    secured = apply_row_level_policy(
        BASE_SQL,
        user_context,
        dialect="bigquery",
        policy_columns={"region": "country"},
    )
    trace.get_current_span().set_attribute("db.query.text", secured)
    print(f"   rewritten: {secured}")
    return bq.run(secured)


def main() -> None:
    """Demonstrate tenant scoping and explicit admin role bypass."""
    print(f"\nbase SQL:\n   {BASE_SQL}\n")
    for user, context in USERS.items():
        with bind_execution_scope(user_id=user, claims=context):
            print(f"{user} context={context}")
            rows = secure_query(context)
            print(f"   BigQuery rows: {rows}\n")
    tel.flush()


if __name__ == "__main__":
    main()
