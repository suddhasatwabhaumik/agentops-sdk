"""BigQuery `agentops_tutorial.dealer_stock` setup (tutorials 05, 06, 14).

Creates the dataset and table and seeds a few rows if empty.
"""

from __future__ import annotations

from google.cloud import bigquery

ROWS = [
    {
        "dealer_id": "dealer-4711",
        "country": "Germany",
        "part_number": "PART-1001",
        "units_available": 7,
        "warehouse": "Plant-EU-01",
    },
    {
        "dealer_id": "dealer-4711",
        "country": "Germany",
        "part_number": "PART-1002",
        "units_available": 0,
        "warehouse": "Plant-EU-01",
    },
    {
        "dealer_id": "dealer-0815",
        "country": "Brazil",
        "part_number": "PART-1001",
        "units_available": 12,
        "warehouse": "Plant-SA-01",
    },
    {
        "dealer_id": "dealer-0815",
        "country": "Brazil",
        "part_number": "PART-1003",
        "units_available": 3,
        "warehouse": "Plant-SA-01",
    },
    {
        "dealer_id": "dealer-2200",
        "country": "France",
        "part_number": "PART-1001",
        "units_available": 1,
        "warehouse": "Plant-EU-02",
    },
]
SCHEMA = [
    bigquery.SchemaField("dealer_id", "STRING"),
    bigquery.SchemaField("country", "STRING"),
    bigquery.SchemaField("part_number", "STRING"),
    bigquery.SchemaField("units_available", "INT64"),
    bigquery.SchemaField("warehouse", "STRING"),
]


def ensure(
    project: str, location: str, dataset_id: str = "agentops_tutorial"
) -> str:
    client = bigquery.Client(project=project, location=location)
    dataset = bigquery.Dataset(f"{project}.{dataset_id}")
    dataset.location = location
    client.create_dataset(
        dataset, exists_ok=True
    )  # needs bigquery.datasets.create
    table_id = f"{project}.{dataset_id}.dealer_stock"
    table = client.create_table(
        bigquery.Table(table_id, schema=SCHEMA), exists_ok=True
    )
    if table.num_rows == 0:
        errors = client.insert_rows_json(
            table_id, ROWS
        )  # needs bigquery.tables.updateData
        if errors:
            raise RuntimeError(f"insert errors: {errors}")
    return f"{table_id} ready ({len(ROWS)} rows seeded if empty)"
