#!/usr/bin/env python3
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Step 1: BigQuery dataset provisioning and synthetic feature seeding."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import datetime
import logging
import sys
from typing import Any

from google.cloud import bigquery
from google.cloud import exceptions as cloud_exceptions

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions

logger = logging.getLogger("migration-step1")

_CATEGORIES = (
    "Tier_A",
    "Tier_B",
    "Tier_C",
    "Standard",
    "Priority",
    "Enterprise",
)
_BQ_TYPE_MAP = {
    "INT": "INTEGER",
    "INT64": "INTEGER",
    "BIGINT": "INTEGER",
    "DOUBLE": "FLOAT",
    "REAL": "FLOAT",
    "NUMERIC": "FLOAT",
    "BOOL": "BOOLEAN",
    "TEXT": "STRING",
    "VARCHAR": "STRING",
    "CHAR": "STRING",
}


def build_feature_schema(
    entity_id_column: str = "entity_id",
    timestamp_column: str = "feature_timestamp",
    feature_columns: Mapping[str, str] | None = None,
) -> list[bigquery.SchemaField]:
    """Constructs BigQuery SchemaField definitions for synthetic features."""
    fields = [
        bigquery.SchemaField(entity_id_column, "STRING", mode="REQUIRED"),
        bigquery.SchemaField(timestamp_column, "TIMESTAMP", mode="REQUIRED"),
    ]
    if feature_columns:
        for col_name, col_type in feature_columns.items():
            mapped = _BQ_TYPE_MAP.get(col_type.upper(), col_type.upper())
            fields.append(
                bigquery.SchemaField(col_name, mapped, mode="NULLABLE")
            )
        return fields
    default_cols = (
        ("stock_level", "INTEGER"),
        ("lead_time_days", "INTEGER"),
        ("safety_stock_threshold", "INTEGER"),
        ("supplier_reliability_score", "FLOAT"),
        ("category", "STRING"),
        ("reorder_point", "INTEGER"),
        ("is_critical", "BOOLEAN"),
    )
    for col_name, col_type in default_cols:
        fields.append(bigquery.SchemaField(col_name, col_type, mode="NULLABLE"))
    return fields


def generate_dummy_features(
    num_records: int = 50,
    prefix: str = "ENTITY",
    entity_id_column: str = "entity_id",
    timestamp_column: str = "feature_timestamp",
) -> list[dict[str, Any]]:
    """Generates deterministic synthetic feature rows for BigQuery seeding."""
    if num_records < 1:
        raise ValueError("num_records must be >= 1.")
    base_time = datetime.datetime.now(datetime.UTC)
    records: list[dict[str, Any]] = []
    for idx in range(1, num_records + 1):
        ts_iso = (base_time - datetime.timedelta(minutes=idx * 2)).isoformat()
        stock_level = 10 + (idx * 29) % 490
        safety_stock = 15 + (idx * 7) % 60
        reorder_point = safety_stock + 5 + (idx % 15)
        reliability = round(0.80 + ((idx * 3) % 20) * 0.01, 3)
        records.append(
            {
                entity_id_column: f"{prefix}-{1000 + idx}",
                timestamp_column: ts_iso,
                "stock_level": stock_level,
                "lead_time_days": 2 + (idx * 5) % 40,
                "safety_stock_threshold": safety_stock,
                "supplier_reliability_score": reliability,
                "category": _CATEGORIES[idx % len(_CATEGORIES)],
                "reorder_point": reorder_point,
                "is_critical": stock_level < reorder_point
                or reliability < 0.88,
            }
        )
    return records


def ensure_bigquery_dataset_and_table(
    bq_client: Any,
    project_id: str,
    region: str,
    dataset_id: str,
    table_id: str,
    *,
    entity_id_column: str = "entity_id",
    timestamp_column: str = "feature_timestamp",
) -> dict[str, str]:
    """Idempotently creates the target BigQuery dataset and feature table."""
    ds_ref = bigquery.DatasetReference(project_id, dataset_id)
    try:
        bq_client.get_dataset(ds_ref)
    except cloud_exceptions.NotFound:
        ds = bigquery.Dataset(ds_ref)
        ds.location = region
        bq_client.create_dataset(ds, exists_ok=True)
    tbl_ref = bigquery.TableReference(ds_ref, table_id)
    try:
        bq_client.get_table(tbl_ref)
    except cloud_exceptions.NotFound:
        schema = build_feature_schema(entity_id_column, timestamp_column)
        tbl = bigquery.Table(tbl_ref, schema=schema)
        tbl.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field=timestamp_column,
        )
        bq_client.create_table(tbl, exists_ok=True)
    uri = f"bq://{project_id}.{dataset_id}.{table_id}"
    return {"status": "ready", "table_uri": uri}


def load_records_to_bigquery(
    bq_client: Any,
    project_id: str,
    dataset_id: str,
    table_id: str,
    records: Sequence[Mapping[str, Any]],
    *,
    chunk_size: int = 500,
) -> int:
    """Streams synthetic feature rows into BigQuery in bounded batches."""
    table_ref = f"{project_id}.{dataset_id}.{table_id}"
    total = 0
    for start in range(0, len(records), chunk_size):
        batch = [dict(row) for row in records[start : start + chunk_size]]
        errors = bq_client.insert_rows_json(table_ref, batch)
        if errors:
            raise exceptions.FeatureStoreError(
                f"BigQuery insert_rows_json errors: {errors}"
            )
        total += len(batch)
    return total


def seed_dummy_data(
    dataset_id: str,
    table_id: str,
    *,
    project_id: str | None = None,
    region: str | None = None,
    num_records: int = 50,
    prefix: str = "ENTITY",
    dry_run: bool = False,
    bq_client: Any | None = None,
) -> dict[str, Any]:
    """Provisions BigQuery feature table and loads synthetic rows."""
    clean_ds = settings.validate_gcp_identifier(dataset_id, "dataset_id")
    clean_tbl = settings.validate_gcp_identifier(table_id, "table_id")
    proj, loc = settings.resolve_gcp_project_and_location(
        project_id, region, require_project=not dry_run
    )
    resolved_proj = str(proj or "dry-run-project")
    records = generate_dummy_features(num_records=num_records, prefix=prefix)
    uri = f"bq://{resolved_proj}.{clean_ds}.{clean_tbl}"
    if dry_run:
        logger.info("[Dry-Run] Would seed %d rows into %s.", len(records), uri)
        return {
            "status": "simulated",
            "table_uri": uri,
            "records_loaded": len(records),
        }
    client = bq_client or bigquery.Client(project=resolved_proj)
    ensure_bigquery_dataset_and_table(
        client, resolved_proj, loc, clean_ds, clean_tbl
    )
    loaded = load_records_to_bigquery(
        client, resolved_proj, clean_ds, clean_tbl, records
    )
    logger.info("Loaded %d synthetic records into %s.", loaded, uri)
    return {"status": "ready", "table_uri": uri, "records_loaded": loaded}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parses CLI arguments for Step 1 BigQuery seeding."""
    parser = argparse.ArgumentParser(
        description="STEP 1: BigQuery Dataset & Synthetic Feature Seeding"
    )
    parser.add_argument("--project", dest="project_id", default=None)
    parser.add_argument("--region", dest="region", default=None)
    parser.add_argument("--dataset", dest="dataset_id", required=True)
    parser.add_argument("--table", dest="table_id", required=True)
    parser.add_argument(
        "--num-records", dest="num_records", type=int, default=50
    )
    parser.add_argument("--prefix", dest="prefix", default="ENTITY")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint for Step 1 BigQuery synthetic feature seeding."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    args = parse_args(argv)
    try:
        res = seed_dummy_data(
            dataset_id=args.dataset_id,
            table_id=args.table_id,
            project_id=args.project_id,
            region=args.region,
            num_records=args.num_records,
            prefix=args.prefix,
            dry_run=args.dry_run,
        )
        print(
            f"Step 1 Complete ({res['status']}): {res['records_loaded']} "
            f"records -> {res['table_uri']}"
        )
    except (exceptions.AgentOpsError, ValueError) as exc:
        logger.error("Step 1 execution failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
