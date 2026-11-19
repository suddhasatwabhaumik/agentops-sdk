#!/usr/bin/env python3
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Step 5: Verify Vertex AI Feature Online Store low-latency lookups."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import logging
import sys
import time
from typing import Any

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.feature_store import in_memory
from agentops_sdk.feature_store import online_client
from agentops_sdk.ports import feature_store as fs_ports

logger = logging.getLogger("verify-online-lookup")


def _build_dry_run_store(
    online_store_id: str,
    feature_view_id: str,
    entity_ids: Sequence[str],
) -> in_memory.InMemoryFeatureOnlineStore:
    store = in_memory.InMemoryFeatureOnlineStore()
    seeded = {
        eid: {
            "stock_level": 100 + idx * 10,
            "lead_time_days": 5 + idx,
            "is_critical": False,
        }
        for idx, eid in enumerate(entity_ids, start=1)
    }
    store.seed_features(online_store_id, feature_view_id, seeded)
    return store


def _resolve_store_client(
    store_id: str,
    view_id: str,
    entity_ids: Sequence[str],
    project_id: str | None,
    region: str | None,
    *,
    dry_run: bool,
    store_client: fs_ports.FeatureOnlineStorePort | None,
) -> fs_ports.FeatureOnlineStorePort:
    if store_client is not None:
        return store_client
    if dry_run:
        return _build_dry_run_store(store_id, view_id, entity_ids)
    return online_client.FeatureStoreOnlineClient(
        project_id=str(project_id), location=region
    )


def verify_online_lookup(
    online_store_id: str,
    feature_view_id: str,
    *,
    entity_ids: Sequence[str] = ("CUST_0001", "CUST_0002"),
    features: Sequence[str] | None = None,
    project_id: str | None = None,
    region: str | None = None,
    dry_run: bool = False,
    store_client: fs_ports.FeatureOnlineStorePort | None = None,
) -> dict[str, Any]:
    """Reads online features via FeatureStoreOnlineClient or InMemory store."""
    store = settings.validate_gcp_identifier(online_store_id, "online_store_id")
    view = settings.validate_gcp_identifier(feature_view_id, "feature_view_id")
    proj, loc = settings.resolve_gcp_project_and_location(
        project_id, region, require_project=not dry_run
    )
    client = _resolve_store_client(
        store,
        view,
        entity_ids,
        proj,
        loc,
        dry_run=dry_run,
        store_client=store_client,
    )
    start = time.perf_counter()
    records = client.read_online_features(
        store,
        view,
        entity_ids=list(entity_ids),
        features=list(features) if features else None,
    )
    latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
    return {
        "status": "simulated" if dry_run else "verified",
        "latency_ms": latency_ms,
        "records": records,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parses CLI arguments for Step 5 online lookup verification."""
    parser = argparse.ArgumentParser(
        description="STEP 5: Verify Vertex AI Feature Online Store Lookup"
    )
    parser.add_argument("--project", dest="project_id", default=None)
    parser.add_argument("--region", dest="region", default=None)
    parser.add_argument("--online-store", dest="online_store_id", required=True)
    parser.add_argument("--feature-view", dest="feature_view_id", required=True)
    parser.add_argument(
        "--entity-ids",
        dest="entity_ids",
        nargs="+",
        default=["CUST_0001", "CUST_0002"],
    )
    parser.add_argument("--features", dest="features", nargs="*", default=None)
    parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint for Step 5 online feature lookup verification."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    args = parse_args(argv)
    try:
        res = verify_online_lookup(
            online_store_id=args.online_store_id,
            feature_view_id=args.feature_view_id,
            entity_ids=args.entity_ids,
            features=args.features,
            project_id=args.project_id,
            region=args.region,
            dry_run=args.dry_run,
        )
        print(json.dumps(res, indent=2, default=str))
    except (exceptions.AgentOpsError, ValueError) as exc:
        logger.error("Step 5 execution failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
