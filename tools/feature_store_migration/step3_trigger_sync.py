#!/usr/bin/env python3
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Step 3: Trigger BigQuery to Vertex AI Feature Online Store sync CLI."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import logging
import sys

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.feature_store import admin

logger = logging.getLogger("migration-step3")


def trigger_feature_view_sync(
    online_store_id: str,
    feature_view_id: str,
    *,
    project_id: str | None = None,
    region: str | None = None,
    wait_for_completion: bool = False,
    timeout_seconds: float = 300.0,
    dry_run: bool = False,
    admin_client: admin.FeatureStoreAdminClient | None = None,
) -> dict[str, str]:
    """Triggers an on-demand FeatureView sync and optionally waits."""
    store = settings.validate_gcp_identifier(online_store_id, "online_store_id")
    view = settings.validate_gcp_identifier(feature_view_id, "feature_view_id")
    proj, loc = settings.resolve_gcp_project_and_location(
        project_id, region, require_project=not dry_run
    )
    if dry_run:
        logger.info("[Dry-Run] Would sync %s/%s in %s.", store, view, loc)
        state = "SUCCEEDED" if wait_for_completion else "RUNNING"
        return {"sync_name": f"simulated/{store}/{view}", "sync_state": state}
    client = admin_client or admin.FeatureStoreAdminClient(str(proj), loc)
    res: dict[str, str] = client.trigger_sync(
        store,
        view,
        wait_for_completion=wait_for_completion,
        timeout_seconds=timeout_seconds,
    )
    return res


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parses CLI arguments for Step 3 sync triggering."""
    parser = argparse.ArgumentParser(
        description="STEP 3: Trigger Vertex AI FeatureView Sync"
    )
    parser.add_argument("--project", dest="project_id", default=None)
    parser.add_argument("--region", dest="region", default=None)
    parser.add_argument("--online-store", dest="online_store_id", required=True)
    parser.add_argument("--feature-view", dest="feature_view_id", required=True)
    parser.add_argument("--wait", dest="wait", action="store_true")
    parser.add_argument("--timeout", dest="timeout", type=int, default=300)
    parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint for Step 3 FeatureView sync triggering."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    args = parse_args(argv)
    try:
        res = trigger_feature_view_sync(
            online_store_id=args.online_store_id,
            feature_view_id=args.feature_view_id,
            project_id=args.project_id,
            region=args.region,
            wait_for_completion=args.wait,
            timeout_seconds=float(args.timeout),
            dry_run=args.dry_run,
        )
        print(f"Step 3 Complete: Sync state={res.get('sync_state', 'RUNNING')}")
    except (exceptions.AgentOpsError, ValueError) as exc:
        logger.error("Step 3 execution failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
