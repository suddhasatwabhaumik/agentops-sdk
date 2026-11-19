#!/usr/bin/env python3
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""ADK Data Science Agent with flow and LLM telemetry decorators."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from agentops_sdk import bind_execution_scope
from agentops_sdk import log_llm_metrics
from agentops_sdk import trace_agent_flow
from agentops_sdk import trace_llm_call

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("adk-finops-agent")


class ADKDataScienceFinOpsAgent:
    """Data Science agent emitting v1 OTel spans and flat FinOps payloads."""

    def __init__(
        self,
        project_id: str,
        dataset_id: str = "agent_feature_store",
        table_id: str = "customer_features",
        *,
        offline: bool = False,
    ) -> None:
        self.project_id = project_id or "local-offline"
        self.dataset_id = dataset_id
        self.table_id = table_id
        self.offline = offline

    @trace_agent_flow(
        agent_role="metadata_explorer",
        flow_name="explore_bigquery_schema",
        kpi_id="supply-chain-efficiency",
        pl_impact_line="inventory-holding-cost",
    )
    def explore_schema(self) -> dict[str, Any]:
        """Inspect BigQuery schema (or simulated schema in --offline)."""
        if self.offline:
            return {
                "project_id": self.project_id,
                "dataset_id": self.dataset_id,
                "table_id": self.table_id,
                "columns": [
                    "customer_id",
                    "daily_demand",
                    "lead_time_days",
                    "safety_stock_units",
                ],
                "row_count": 5000,
                "status": "OFFLINE_SCHEMA_OK",
            }
        from google.cloud import bigquery

        client = bigquery.Client(project=self.project_id)
        table = client.get_table(
            f"{self.project_id}.{self.dataset_id}.{self.table_id}"
        )
        return {
            "project_id": self.project_id,
            "dataset_id": self.dataset_id,
            "table_id": self.table_id,
            "columns": [field.name for field in table.schema],
            "row_count": table.num_rows,
            "status": "LIVE_BIGQUERY_SUCCESS",
        }

    @trace_llm_call(
        kpi_id="supply-chain-efficiency",
        pl_impact_line="inventory-holding-cost",
        model_name_override="gemini-2.5-flash",
    )
    async def generate_analytical_sql(
        self, schema_info: dict[str, Any]
    ) -> dict[str, Any]:
        """Simulate Gemini 2.5 Flash SQL generation with FinOps metrics."""
        await asyncio.sleep(0.02)
        safe_cols = [
            c
            for c in schema_info.get("columns", ["customer_id", "daily_demand"])
            if c.replace("_", "").isalnum()
        ]
        cols_expr = ", ".join(safe_cols) or "*"
        table_ref = f"`{self.project_id}.{self.dataset_id}.{self.table_id}`"
        query = " ".join(
            [
                "SELECT",
                (
                    f"{cols_expr}, AVG(daily_demand) "
                    "OVER(PARTITION BY customer_id)"
                ),
                "FROM",
                table_ref,
            ]
        )
        log_llm_metrics(
            model_name="gemini-2.5-flash",
            input_tokens=420,
            output_tokens=160,
            cached_tokens=200,
        )
        return {
            "model_name": "gemini-2.5-flash",
            "usage": {
                "prompt_token_count": 420,
                "candidates_token_count": 160,
                "cached_content_token_count": 200,
            },
            "finish_reason": "STOP",
            "generated_query": query,
        }

    @trace_llm_call(
        kpi_id="supply-chain-efficiency",
        pl_impact_line="inventory-holding-cost",
        model_name_override="gemini-2.5-pro",
    )
    def synthesize_forecast_report(
        self, query_result: dict[str, Any]
    ) -> dict[str, Any]:
        """Simulate Gemini 2.5 Pro report synthesis with FinOps metrics."""
        del query_result
        time.sleep(0.02)
        log_llm_metrics(
            model_name="gemini-2.5-pro",
            input_tokens=1250,
            output_tokens=380,
        )
        return {
            "model_name": "gemini-2.5-pro",
            "usage": {
                "prompt_token_count": 1250,
                "candidates_token_count": 380,
            },
            "finish_reason": "STOP",
            "forecast_summary": "Safety stock buffer optimal across hubs.",
            "confidence": 0.96,
        }

    @trace_agent_flow(
        agent_role="orchestrator",
        flow_name="adk_master_forecasting_pipeline",
        kpi_id="supply-chain-efficiency",
        pl_impact_line="inventory-holding-cost",
    )
    async def run_pipeline(self) -> dict[str, Any]:
        """Execute full nested pipeline under ExecutionScope."""
        start_t = time.perf_counter()
        schema = self.explore_schema()
        sql_gen = await self.generate_analytical_sql(schema)
        report = self.synthesize_forecast_report(sql_gen)
        return {
            "pipeline_status": "SUCCESS",
            "project_id": self.project_id,
            "schema": schema,
            "sql_generation": sql_gen,
            "forecast_report": report,
            "total_latency_ms": round(
                (time.perf_counter() - start_t) * 1000, 2
            ),
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-id",
        default=os.getenv("GOOGLE_CLOUD_PROJECT", ""),
    )
    parser.add_argument("--dataset-id", default="agent_feature_store")
    parser.add_argument("--table-id", default="customer_features")
    parser.add_argument("--offline", action="store_true")
    return parser.parse_args()


async def main() -> None:
    """Run ADK FinOps tracing workflow and verify captured OTel spans."""
    args = _parse_args()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    agent = ADKDataScienceFinOpsAgent(
        project_id=args.project_id,
        dataset_id=args.dataset_id,
        table_id=args.table_id,
        offline=args.offline or not args.project_id,
    )
    with bind_execution_scope(
        session_id="sess-finops-e2e", user_id="analyst@example.com"
    ):
        result = await agent.run_pipeline()
    print(json.dumps(result, indent=2))

    spans = exporter.get_finished_spans()
    assert len(spans) >= 4, f"Expected >= 4 spans, got {len(spans)}"
    roles = {s.attributes.get("agentops.agent.role") for s in spans}
    assert {"metadata_explorer", "orchestrator"} <= roles
    provider.shutdown()
    print("[SUCCESS] Nested OTel span hierarchy and FinOps metadata verified.")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
