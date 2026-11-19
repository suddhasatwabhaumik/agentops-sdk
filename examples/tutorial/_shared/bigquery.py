"""Runs a query on BigQuery and returns rows as dicts. Shared by tutorials 05, 06, 14, 15."""

from __future__ import annotations

import os
from typing import Any

from google.cloud import bigquery

_client: bigquery.Client | None = None


def client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            # TUTORIAL_BQ_LOCATION only matters when TUTORIAL_BQ_TABLE points at another region.
            location=os.getenv(
                "TUTORIAL_BQ_LOCATION",
                os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west4"),
            ),
        )
    return _client


def tutorial_table() -> str:
    """Fully qualified table the SQL tutorials read. Created by 00_setup/provision.py bigquery."""
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    dataset = os.getenv("TUTORIAL_BQ_DATASET", "agentops_tutorial")
    return os.getenv("TUTORIAL_BQ_TABLE", f"{project}.{dataset}.dealer_stock")


def run(sql: str, max_rows: int = 20) -> list[dict[str, Any]]:
    """Executes `sql` as a real BigQuery job (billed to GOOGLE_CLOUD_PROJECT) and returns rows."""
    job = client().query(sql)
    return [dict(row.items()) for row in job.result(max_results=max_rows)]


def columns(table: str) -> list[str]:
    """Column names of a table, read from BigQuery metadata (what an agent gives the LLM)."""
    return [field.name for field in client().get_table(table).schema]
