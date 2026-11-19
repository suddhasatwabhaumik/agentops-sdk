#!/usr/bin/env python3
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unified Vertex AI Feature Store Migration & Sync Orchestrator CLI."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import dataclasses
import json
import logging
import sys
from typing import Any

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.feature_store import admin
from tools.feature_store_migration import step1_seed_dummy_data
from tools.feature_store_migration import step2_setup_feature_store
from tools.feature_store_migration import step3_trigger_sync
from tools.feature_store_migration import step4_manage_sync_schedule

logger = logging.getLogger("sync-orchestrator")


@dataclasses.dataclass(frozen=True, slots=True)
class SyncRunConfig:
    """Configuration for a Feature Store end-to-end sync run."""

    project_id: str
    region: str
    dataset_id: str
    table_id: str
    online_store_id: str
    feature_view_id: str
    load_dummy_data: bool = False
    num_records: int = 50
    sync_cron: str | None = None
    wait_for_sync: bool = False
    dry_run: bool = False


def _run_step1_and_step2(
    cfg: SyncRunConfig,
    bq_client: Any | None,
    admin_client: admin.FeatureStoreAdminClient | None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "project_id": cfg.project_id,
        "region": cfg.region,
        "dry_run": cfg.dry_run,
    }
    if cfg.load_dummy_data:
        summary["step1_seed"] = step1_seed_dummy_data.seed_dummy_data(
            cfg.dataset_id,
            cfg.table_id,
            project_id=cfg.project_id,
            region=cfg.region,
            num_records=cfg.num_records,
            dry_run=cfg.dry_run,
            bq_client=bq_client,
        )
    summary["step2_setup"] = step2_setup_feature_store.setup_feature_store(
        cfg.dataset_id,
        cfg.table_id,
        cfg.online_store_id,
        cfg.feature_view_id,
        project_id=cfg.project_id,
        region=cfg.region,
        sync_cron=cfg.sync_cron or None,
        wait_for_completion=True,
        dry_run=cfg.dry_run,
        admin_client=admin_client,
    )
    return summary


def _run_step3_and_step4(
    cfg: SyncRunConfig,
    admin_client: admin.FeatureStoreAdminClient | None,
    step2_setup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "step3_sync": step3_trigger_sync.trigger_feature_view_sync(
            cfg.online_store_id,
            cfg.feature_view_id,
            project_id=cfg.project_id,
            region=cfg.region,
            wait_for_completion=cfg.wait_for_sync,
            dry_run=cfg.dry_run,
            admin_client=admin_client,
        )
    }
    if cfg.sync_cron == "":
        out["step4_schedule"] = step4_manage_sync_schedule.manage_sync_schedule(
            cfg.online_store_id,
            cfg.feature_view_id,
            project_id=cfg.project_id,
            region=cfg.region,
            disable_schedule=True,
            dry_run=cfg.dry_run,
            admin_client=admin_client,
        )
    elif cfg.sync_cron:
        fv_info = (step2_setup or {}).get("feature_view", {})
        fv_name = (
            fv_info.get("name") if isinstance(fv_info, dict) else None
        ) or cfg.feature_view_id
        out["step4_schedule"] = {
            "feature_view": str(fv_name),
            "cron_schedule": cfg.sync_cron,
            "configured_in_step2": True,
        }
    return out


def orchestrate_sync(
    dataset_id: str,
    table_id: str,
    online_store_id: str,
    feature_view_id: str,
    *,
    project_id: str | None = None,
    region: str | None = None,
    load_dummy_data: bool = False,
    num_records: int = 50,
    sync_cron: str | None = None,
    wait_for_sync: bool = False,
    dry_run: bool = False,
    bq_client: Any | None = None,
    admin_client: admin.FeatureStoreAdminClient | None = None,
) -> dict[str, Any]:
    """Orchestrates Steps 1-4 for BigQuery to Feature Online Store sync."""
    proj, loc = settings.resolve_gcp_project_and_location(
        project_id, region, require_project=not dry_run
    )
    cfg = SyncRunConfig(
        project_id=str(proj or "dry-run-project"),
        region=loc,
        dataset_id=dataset_id,
        table_id=table_id,
        online_store_id=online_store_id,
        feature_view_id=feature_view_id,
        load_dummy_data=load_dummy_data,
        num_records=num_records,
        sync_cron=sync_cron,
        wait_for_sync=wait_for_sync,
        dry_run=dry_run,
    )
    summary = _run_step1_and_step2(cfg, bq_client, admin_client)
    summary.update(
        _run_step3_and_step4(
            cfg, admin_client, step2_setup=summary.get("step2_setup")
        )
    )
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parses CLI arguments for end-to-end migration orchestration."""
    parser = argparse.ArgumentParser(
        description="VERTEX AI FEATURE STORE MIGRATION & SYNC ORCHESTRATOR"
    )
    parser.add_argument("--project", dest="project_id", default=None)
    parser.add_argument("--region", dest="region", default=None)
    parser.add_argument("--dataset", dest="dataset_id", required=True)
    parser.add_argument("--table", dest="table_id", required=True)
    parser.add_argument("--online-store", dest="online_store_id", required=True)
    parser.add_argument("--feature-view", dest="feature_view_id", required=True)
    parser.add_argument(
        "--load-dummy-data", dest="load_dummy_data", action="store_true"
    )
    parser.add_argument(
        "--num-records", dest="num_records", type=int, default=50
    )
    parser.add_argument("--sync-cron", dest="sync_cron", default=None)
    parser.add_argument("--wait", dest="wait", action="store_true")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint for Feature Store migration orchestration."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    args = parse_args(argv)
    try:
        result = orchestrate_sync(
            dataset_id=args.dataset_id,
            table_id=args.table_id,
            online_store_id=args.online_store_id,
            feature_view_id=args.feature_view_id,
            project_id=args.project_id,
            region=args.region,
            load_dummy_data=args.load_dummy_data,
            num_records=args.num_records,
            sync_cron=args.sync_cron,
            wait_for_sync=args.wait,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2, default=str))
    except (exceptions.AgentOpsError, ValueError) as exc:
        logger.error("Orchestration failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
