#!/usr/bin/env python3
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""ADK Data Science Agent with AgentOps SDK v1 Feature Store Online Client."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from typing import Any

from agentops_sdk import FeatureStoreOnlineClient
from agentops_sdk import InMemoryFeatureOnlineStore
from agentops_sdk import bind_execution_scope
from agentops_sdk import configure_telemetry
from agentops_sdk import reset_telemetry
from agentops_sdk import trace_agent_span

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("adk-feature-store-agent")

_OFFLINE_SEED: dict[str, dict[str, Any]] = {
    "ENTITY-1001": {
        "stock_level": 142,
        "lead_time_days": 5,
        "warehouse_zone": "EU-WEST4",
    },
    "ENTITY-1002": {
        "stock_level": 28,
        "lead_time_days": 12,
        "warehouse_zone": "US-CENTRAL1",
    },
    "ENTITY-1003": {
        "stock_level": 0,
        "lead_time_days": 21,
        "warehouse_zone": "EU-WEST4",
    },
}


def _to_tabular(
    feature_map: dict[str, dict[str, Any]],
) -> tuple[list[str], list[list[Any]]]:
    """Converts SDK entity->feature dict to ordered tabular columns/rows."""
    feature_names: list[str] = []
    seen: set[str] = set()
    for attrs in feature_map.values():
        for name in attrs:
            if name not in seen:
                seen.add(name)
                feature_names.append(name)
    columns = ["entity_id", *feature_names]
    rows = [
        [eid, *(attrs.get(col) for col in feature_names)]
        for eid, attrs in feature_map.items()
    ]
    return columns, rows


def _build_workflow_report(
    mode: str,
    bq: tuple[list[str], list[list[Any]], float],
    fs: tuple[list[str], list[list[Any]], float],
    proj: tuple[list[str], list[list[Any]], float],
) -> dict[str, Any]:
    """Formats benchmark comparison report from OLAP and Feature Store runs."""
    bq_cols, bq_rows, bq_ms = bq
    fs_cols, fs_rows, fs_ms = fs
    p_cols, p_rows, p_ms = proj
    sample = dict(zip(fs_cols, fs_rows[0], strict=True)) if fs_rows else {}
    return {
        "mode": mode,
        "bigquery_analytics": {
            "columns": bq_cols,
            "rows_count": len(bq_rows),
            "latency_ms": round(bq_ms, 2),
        },
        "feature_store_lookup": {
            "columns": fs_cols,
            "rows_count": len(fs_rows),
            "latency_ms": round(fs_ms, 2),
            "sample_record": sample,
        },
        "explicit_projection": {
            "columns": p_cols,
            "rows_count": len(p_rows),
            "latency_ms": round(p_ms, 2),
        },
        "latency_comparison": {
            "bigquery_ms": round(bq_ms, 2),
            "feature_store_ms": round(fs_ms, 2),
            "speedup_factor": round(bq_ms / max(fs_ms, 0.001), 2),
        },
    }


class ADKDataScienceAgentWithFeatureStore:
    """ADK Data Science Agent backed by AgentOps SDK v1 Feature Store."""

    def __init__(
        self,
        project_id: str,
        region: str,
        online_store_id: str,
        feature_view_id: str,
        bq_dataset_id: str,
        bq_table_id: str,
        *,
        offline: bool = False,
    ) -> None:
        self.project_id = project_id
        self.region = region
        self.online_store_id = online_store_id
        self.feature_view_id = feature_view_id
        self.bq_dataset_id = bq_dataset_id
        self.bq_table_id = bq_table_id
        self.offline = offline
        self._online_client = self._build_online_client()

    def _build_online_client(self) -> Any:
        if self.offline:
            store = InMemoryFeatureOnlineStore()
            store.seed_features(
                self.online_store_id,
                self.feature_view_id,
                _OFFLINE_SEED,
            )
            return store
        return FeatureStoreOnlineClient(
            project_id=self.project_id,
            location=self.region,
        )

    @trace_agent_span(agent_name="adk_agent_fetch_online_features")
    def get_entity_features_for_agent(
        self,
        entity_ids: list[str],
        features: list[str] | None = None,
    ) -> tuple[list[str], list[list[Any]], float]:
        """Fetches real-time features using FeatureStoreOnlineClient."""
        logger.info("[ADK Tool] Fetching online features for %s", entity_ids)
        t0 = time.perf_counter()
        result = self._online_client.read_online_features(
            self.online_store_id,
            self.feature_view_id,
            entity_ids=entity_ids,
            features=features,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        cols, rows = _to_tabular(result)
        return cols, rows, elapsed_ms

    @trace_agent_span(agent_name="adk_agent_run_bigquery_analytics")
    def run_bigquery_sql_analysis(
        self,
        sql_query: str,
    ) -> tuple[list[str], list[list[Any]], float]:
        """Runs analytical SQL against BigQuery (or explicit offline seed)."""
        logger.info("[ADK Tool] Running BigQuery SQL query...")
        t0 = time.perf_counter()
        if self.offline:
            cols = ["entity_id", "stock_level", "lead_time_days"]
            rows = [
                [eid, data["stock_level"], data["lead_time_days"]]
                for eid, data in _OFFLINE_SEED.items()
            ]
        else:
            from google.cloud import bigquery

            bq_client = bigquery.Client(project=self.project_id)
            result = bq_client.query(sql_query).result()
            cols = [field.name for field in result.schema]
            rows = [list(row.values()) for row in result]
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return cols, rows, elapsed_ms

    @trace_agent_span(agent_name="adk_data_science_workflow")
    def execute_agent_workflow(
        self,
        target_entities: list[str],
    ) -> dict[str, Any]:
        """Combines BigQuery OLAP scan and Online Feature Store point lookup."""
        table_fqn = f"{self.project_id}.{self.bq_dataset_id}.{self.bq_table_id}"
        sql_query = f"SELECT * FROM `{table_fqn}` LIMIT 10"  # noqa: S608
        bq = self.run_bigquery_sql_analysis(sql_query)
        fs = self.get_entity_features_for_agent(target_entities)
        proj_features = ["stock_level"] if "stock_level" in fs[0] else None
        proj = self.get_entity_features_for_agent(
            target_entities[:1], features=proj_features
        )
        mode = "offline" if self.offline else "live_gcp"
        return _build_workflow_report(mode, bq, fs, proj)


def parse_args() -> argparse.Namespace:
    """Parses CLI flags for live or offline Feature Store E2E run."""
    parser = argparse.ArgumentParser(
        description="Run ADK Data Science Agent with AgentOps Feature Store"
    )
    parser.add_argument("--project", dest="project_id", default=None)
    parser.add_argument("--region", default="europe-west4")
    parser.add_argument(
        "--online-store",
        dest="online_store_id",
        default="agent_online_store",
    )
    parser.add_argument(
        "--feature-view",
        dest="feature_view_id",
        default="customer_features_view",
    )
    parser.add_argument(
        "--dataset",
        dest="dataset_id",
        default="agent_feature_store",
    )
    parser.add_argument(
        "--table",
        dest="table_id",
        default="customer_features",
    )
    parser.add_argument(
        "--entities",
        nargs="+",
        default=["ENTITY-1001", "ENTITY-1002", "ENTITY-1003"],
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use InMemoryFeatureOnlineStore instead of live GCP APIs.",
    )
    return parser.parse_args()


def main() -> None:
    """Executes Feature Store E2E verification with AgentOps telemetry."""
    args = parse_args()
    project_id = (
        args.project_id
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or ("local-offline" if args.offline else "")
    )
    if not project_id and not args.offline:
        raise ValueError(
            "GCP project ID is required unless --offline is specified."
        )
    configure_telemetry(mode="off")
    try:
        with bind_execution_scope(
            session_id="e2e-feature-store-001",
            user_id="e2e-operator",
        ):
            agent = ADKDataScienceAgentWithFeatureStore(
                project_id=project_id,
                region=args.region,
                online_store_id=args.online_store_id,
                feature_view_id=args.feature_view_id,
                bq_dataset_id=args.dataset_id,
                bq_table_id=args.table_id,
                offline=args.offline,
            )
            results = agent.execute_agent_workflow(args.entities)
    finally:
        reset_telemetry()
    print(json.dumps(results, indent=2))
    fs_results = results["feature_store_lookup"]
    assert fs_results["rows_count"] > 0, "No Feature Store records returned!"
    assert len(fs_results["columns"]) >= 2, "Insufficient columns returned!"
    assert "entity_id" in fs_results["columns"], "Missing 'entity_id' column!"
    print("[VERIFICATION PASSED]: Feature Store v1 E2E workflow succeeded.")


if __name__ == "__main__":
    main()
