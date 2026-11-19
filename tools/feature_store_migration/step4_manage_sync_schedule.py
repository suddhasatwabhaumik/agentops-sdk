#!/usr/bin/env python3
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Step 4: Manage Vertex AI FeatureView in-engine sync schedule CLI."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import logging
import sys
from typing import Any

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.feature_store import admin

logger = logging.getLogger("manage-sync-schedule")


def _dry_run_schedule(
    store: str,
    view: str,
    set_cron: str | None,
    *,
    disable_schedule: bool,
    get_schedule: bool,
) -> dict[str, Any]:
    cron_val = None if (disable_schedule or get_schedule) else set_cron
    logger.info("[Dry-Run] Schedule %s/%s -> %r.", store, view, cron_val)
    result: dict[str, Any] = {
        "feature_view": view,
        "cron_schedule": cron_val,
    }
    if get_schedule:
        result["last_sync_name"] = None
        result["last_sync_state"] = "UNKNOWN (dry-run)"
    return result


def manage_sync_schedule(
    online_store_id: str,
    feature_view_id: str,
    *,
    project_id: str | None = None,
    region: str | None = None,
    get_schedule: bool = False,
    set_cron: str | None = None,
    disable_schedule: bool = False,
    dry_run: bool = False,
    admin_client: admin.FeatureStoreAdminClient | None = None,
) -> dict[str, Any]:
    """Sets, disables, or inspects a FeatureView in-engine sync schedule."""
    if get_schedule and (disable_schedule or set_cron):
        raise ValueError("Pass --get without --set-cron or --disable.")
    if disable_schedule and set_cron:
        raise ValueError("Pass either set_cron or disable_schedule, not both.")
    store = settings.validate_gcp_identifier(online_store_id, "online_store_id")
    view = settings.validate_gcp_identifier(feature_view_id, "feature_view_id")
    proj, loc = settings.resolve_gcp_project_and_location(
        project_id, region, require_project=not dry_run
    )
    if dry_run:
        return _dry_run_schedule(
            store,
            view,
            set_cron,
            disable_schedule=disable_schedule,
            get_schedule=get_schedule,
        )
    client = admin_client or admin.FeatureStoreAdminClient(str(proj), loc)
    if not get_schedule:
        if disable_schedule:
            client.set_sync_schedule(store, view, cron_schedule=None)
        elif set_cron:
            client.set_sync_schedule(store, view, cron_schedule=set_cron)
    schedule: dict[str, Any] = client.get_sync_schedule(store, view)
    return schedule


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parses CLI arguments for Step 4 schedule management."""
    parser = argparse.ArgumentParser(
        description="STEP 4: Manage FeatureView In-Engine Sync Schedule"
    )
    parser.add_argument("--project", dest="project_id", default=None)
    parser.add_argument("--region", dest="region", default=None)
    parser.add_argument("--online-store", dest="online_store_id", required=True)
    parser.add_argument("--feature-view", dest="feature_view_id", required=True)
    parser.add_argument("--get", dest="get_schedule", action="store_true")
    parser.add_argument("--set-cron", dest="set_cron", default=None)
    parser.add_argument(
        "--disable", dest="disable_schedule", action="store_true"
    )
    parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint for Step 4 sync schedule management."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    args = parse_args(argv)
    try:
        info = manage_sync_schedule(
            online_store_id=args.online_store_id,
            feature_view_id=args.feature_view_id,
            project_id=args.project_id,
            region=args.region,
            get_schedule=args.get_schedule,
            set_cron=args.set_cron,
            disable_schedule=args.disable_schedule,
            dry_run=args.dry_run,
        )
        cron_val = info.get("cron_schedule") or "None (Manual / On-Demand)"
        print(f"Current In-Engine Schedule: {cron_val}")
        if (
            args.get_schedule
            or info.get("last_sync_state")
            or info.get("last_sync_name")
        ):
            sync_name = info.get("last_sync_name") or "None"
            sync_state = info.get("last_sync_state") or "None"
            print(f"Latest Sync: {sync_name} (state={sync_state})")
    except (exceptions.AgentOpsError, ValueError) as exc:
        logger.error("Step 4 execution failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
