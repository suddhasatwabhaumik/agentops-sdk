#!/usr/bin/env python3
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Step 2: Vertex AI Feature Online Store & Feature View provisioning CLI."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import logging
import sys
from typing import Any, Literal

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.feature_store import admin

logger = logging.getLogger("migration-step2")


def _validate_setup_targets(
    dataset_id: str,
    table_id: str,
    online_store_id: str,
    feature_view_id: str,
    project_id: str | None,
    region: str | None,
    *,
    dry_run: bool,
) -> tuple[str, str, str, str, str, str]:
    ds = settings.validate_gcp_identifier(dataset_id, "dataset_id")
    tbl = settings.validate_gcp_identifier(table_id, "table_id")
    store = settings.validate_gcp_identifier(online_store_id, "online_store_id")
    view = settings.validate_gcp_identifier(feature_view_id, "feature_view_id")
    proj, loc = settings.resolve_gcp_project_and_location(
        project_id, region, require_project=not dry_run
    )
    return str(proj or "dry-run-project"), loc, ds, tbl, store, view


def _provision_live(
    client: admin.FeatureStoreAdminClient,
    store_id: str,
    view_id: str,
    bq_uri: str,
    opts: dict[str, Any],
) -> dict[str, Any]:
    store_res = client.ensure_online_store(
        store_id,
        store_type=opts["store_type"],
        min_nodes=opts["min_nodes"],
        max_nodes=opts["max_nodes"],
        wait_for_completion=opts["wait"],
    )
    view_res = client.ensure_feature_view(
        store_id,
        view_id,
        bigquery_uri=bq_uri,
        entity_id_columns=(opts["entity_id_column"],),
        cron_schedule=opts["sync_cron"],
        wait_for_completion=opts["wait"],
    )
    return {
        "status": "ready",
        "online_store": store_res,
        "feature_view": view_res,
        "bigquery_uri": bq_uri,
    }


def _dry_run_result(store: str, view: str, bq_uri: str) -> dict[str, Any]:
    logger.info("[Dry-Run] Would provision %s/%s.", store, view)
    return {
        "status": "simulated",
        "online_store": {"name": store, "created": False},
        "feature_view": {"name": view, "created": False},
        "bigquery_uri": bq_uri,
    }


def setup_feature_store(
    dataset_id: str,
    table_id: str,
    online_store_id: str,
    feature_view_id: str,
    *,
    project_id: str | None = None,
    region: str | None = None,
    store_type: Literal["bigtable", "optimized"] = "bigtable",
    min_nodes: int = 1,
    max_nodes: int = 3,
    sync_cron: str | None = None,
    entity_id_column: str = "entity_id",
    wait_for_completion: bool = False,
    dry_run: bool = False,
    admin_client: admin.FeatureStoreAdminClient | None = None,
) -> dict[str, Any]:
    """Provisions FeatureOnlineStore and FeatureView backed by BigQuery."""
    proj, loc, ds, tbl, store, view = _validate_setup_targets(
        dataset_id,
        table_id,
        online_store_id,
        feature_view_id,
        project_id,
        region,
        dry_run=dry_run,
    )
    bq_uri = f"bq://{proj}.{ds}.{tbl}"
    if dry_run:
        return _dry_run_result(store, view, bq_uri)
    client = admin_client or admin.FeatureStoreAdminClient(proj, loc)
    opts: dict[str, Any] = {
        "store_type": store_type,
        "min_nodes": min_nodes,
        "max_nodes": max_nodes,
        "sync_cron": sync_cron,
        "entity_id_column": entity_id_column,
        "wait": wait_for_completion,
    }
    return _provision_live(client, store, view, bq_uri, opts)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parses CLI arguments for Step 2 Feature Store setup."""
    parser = argparse.ArgumentParser(
        description="STEP 2: Feature Online Store & Feature View Setup"
    )
    parser.add_argument("--project", dest="project_id", default=None)
    parser.add_argument("--region", dest="region", default=None)
    parser.add_argument("--dataset", dest="dataset_id", required=True)
    parser.add_argument("--table", dest="table_id", required=True)
    parser.add_argument("--online-store", dest="online_store_id", required=True)
    parser.add_argument("--feature-view", dest="feature_view_id", required=True)
    parser.add_argument("--sync-cron", dest="sync_cron", default=None)
    parser.add_argument(
        "--store-type",
        dest="store_type",
        choices=("bigtable", "optimized"),
        default="bigtable",
    )
    parser.add_argument("--min-nodes", dest="min_nodes", type=int, default=1)
    parser.add_argument("--max-nodes", dest="max_nodes", type=int, default=3)
    parser.add_argument("--wait", dest="wait", action="store_true")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint for Step 2 Feature Online Store setup."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    args = parse_args(argv)
    try:
        res = setup_feature_store(
            dataset_id=args.dataset_id,
            table_id=args.table_id,
            online_store_id=args.online_store_id,
            feature_view_id=args.feature_view_id,
            project_id=args.project_id,
            region=args.region,
            store_type=args.store_type,
            min_nodes=args.min_nodes,
            max_nodes=args.max_nodes,
            sync_cron=args.sync_cron,
            wait_for_completion=args.wait,
            dry_run=args.dry_run,
        )
        print(f"Step 2 Complete ({res['status']}): {res['bigquery_uri']}")
    except (exceptions.AgentOpsError, ValueError) as exc:
        logger.error("Step 2 execution failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
