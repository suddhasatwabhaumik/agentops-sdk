"""BigQuery query helper returning row dicts (tutorials 02, 05, 06, 14)."""

from __future__ import annotations

from collections.abc import Sequence
import os
from typing import Any

from google.cloud import bigquery

_client: bigquery.Client | None = None


def client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            # TUTORIAL_BQ_LOCATION applies if TUTORIAL_BQ_TABLE is cross-region.
            location=os.getenv(
                "TUTORIAL_BQ_LOCATION",
                os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west4"),
            ),
        )
    return _client


def tutorial_table() -> str:
    """Fully qualified table read by SQL tutorials (`provision.py bigquery`)."""
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    dataset = os.getenv("TUTORIAL_BQ_DATASET", "agentops_tutorial")
    return os.getenv(
        "TUTORIAL_BQ_TABLE", f"{project}.{dataset}.tenant_inventory"
    )


def run(
    sql: str,
    max_rows: int = 20,
    params: Sequence[bigquery.ScalarQueryParameter] | None = None,
) -> list[dict[str, Any]]:
    """Execute `sql` as a BigQuery job and return rows as dicts."""
    job_config = (
        bigquery.QueryJobConfig(query_parameters=list(params))
        if params is not None
        else None
    )
    job = client().query(sql, job_config=job_config)
    return [dict(row.items()) for row in job.result(max_results=max_rows)]


def columns(table: str) -> list[str]:
    """Return column names of a BigQuery table from schema metadata."""
    return [field.name for field in client().get_table(table).schema]
