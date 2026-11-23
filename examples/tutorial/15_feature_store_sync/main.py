# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Tutorial 15: Vertex AI Feature Store sync and online lookup."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared import settings
from _shared import telemetry

cfg = settings.load("15_feature_store_sync")
tel = telemetry.start(cfg)

from google.cloud import bigquery

from agentops_sdk import FeatureStoreAdminClient
from agentops_sdk import FeatureStoreOnlineClient
from agentops_sdk import trace_agent_flow
from agentops_sdk import trace_agent_span

DATASET = os.getenv("TUTORIAL_BQ_DATASET", "agentops_tutorial")
TABLE = "tenant_features"
STORE = os.getenv("TUTORIAL_ONLINE_STORE_ID", "agentops_tutorial_online_store")
VIEW = os.getenv("TUTORIAL_FEATURE_VIEW_ID", "tenant_features_view")
FEATURES = ["sales_30d_eur", "open_backorders", "country"]


def _seed_bigquery_features() -> str:
    """Create and seed the offline BigQuery feature table."""
    client = bigquery.Client(project=cfg.project, location=cfg.location)
    dataset = bigquery.Dataset(f"{cfg.project}.{DATASET}")
    dataset.location = cfg.location
    client.create_dataset(dataset, exists_ok=True)

    table_id = f"{cfg.project}.{DATASET}.{TABLE}"
    schema = [
        bigquery.SchemaField("entity_id", "STRING"),
        bigquery.SchemaField("feature_timestamp", "TIMESTAMP"),
        bigquery.SchemaField("sales_30d_eur", "FLOAT64"),
        bigquery.SchemaField("open_backorders", "INT64"),
        bigquery.SchemaField("country", "STRING"),
    ]
    client.create_table(bigquery.Table(table_id, schema=schema), exists_ok=True)

    now = datetime.now(UTC).isoformat()
    rows = [
        {
            "entity_id": "tenant-4711",
            "feature_timestamp": now,
            "sales_30d_eur": 184250.0,
            "open_backorders": 3,
            "country": "Germany",
        },
        {
            "entity_id": "tenant-0815",
            "feature_timestamp": now,
            "sales_30d_eur": 96100.0,
            "open_backorders": 11,
            "country": "Brazil",
        },
    ]
    errors = client.insert_rows_json(table_id, rows)
    if errors:
        raise RuntimeError(f"BigQuery seed failed: {errors}")
    return table_id


@trace_agent_span(agent_name="tenant_features_tool")
def tenant_features(
    online_client: FeatureStoreOnlineClient, tenant_id: str
) -> dict:
    """Low-latency online feature lookup called by an agent tool."""
    return online_client.read_online_features(
        STORE,
        VIEW,
        entity_ids=[tenant_id],
        features=FEATURES,
    )


@trace_agent_flow(
    flow_name="feature_store_demo",
    kpi_id="tenant-insights",
    pl_impact_line="aftersales-opex",
)
def main() -> None:
    """Seed BigQuery features, provision store/view, sync, and read online."""
    admin = FeatureStoreAdminClient(
        project_id=cfg.project, location=cfg.location
    )
    online = FeatureStoreOnlineClient(
        project_id=cfg.project, location=cfg.location
    )

    print("\n1) offline table in BigQuery")
    table_id = _seed_bigquery_features()
    print(f"   seeded {table_id}")

    print("\n2) online store (Bigtable-backed)")
    print(
        "  ",
        admin.ensure_online_store(
            STORE, min_nodes=1, max_nodes=1, wait_for_completion=True
        ),
    )

    print("\n3) feature view + sync from BigQuery")
    print(
        "  ",
        admin.ensure_feature_view(
            STORE,
            VIEW,
            bigquery_uri=f"bq://{table_id}",
            entity_id_columns=["entity_id"],
        ),
    )
    sync = admin.trigger_sync(
        STORE, VIEW, wait_for_completion=True, timeout_seconds=600
    )
    print("   sync:", sync)
    if sync.get("sync_state") != "SUCCEEDED":
        sys.exit("Feature view sync did not succeed.")

    print("\n4) online read, as an agent tool would do it")
    print("  ", tenant_features(online, "tenant-4711"))


if __name__ == "__main__":
    try:
        main()
    finally:
        tel.flush()
