#!/usr/bin/env python3
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""ADK Data Science Agent protected by v1 @validate_tool_output."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from pydantic import BaseModel
from pydantic import Field

from agentops_sdk import validate_tool_output

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("adk-validation-agent")


class DatasetMetadata(BaseModel):
    """Schema contract for dataset metadata lookup."""

    project_id: str
    dataset_id: str
    table_id: str
    num_columns: int = Field(..., gt=0)
    column_names: list[str] = Field(..., min_length=1)
    status: str = "ACTIVE"


class FeatureObservation(BaseModel):
    """Schema contract for feature store observations."""

    entity_id: str
    metric_value: float
    category: str


class FeatureKPIReport(BaseModel):
    """Schema contract for aggregated feature KPI metrics."""

    kpi_name: str
    total_samples: int = Field(..., ge=0)
    average_score: float = Field(..., ge=0.0, le=100.0)
    anomalies_detected: int = Field(0, ge=0)


class DriftBenchmark(BaseModel):
    """Schema contract for model drift benchmark check."""

    metric: str
    psi_score: float = Field(..., ge=0.0)
    is_drift_detected: bool
    confidence: float = Field(..., ge=0.0, le=1.0)


class ADKDataScienceValidationAgent:
    """Data Science agent tools validated with Pydantic and guards."""

    def __init__(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        *,
        offline: bool = False,
    ) -> None:
        self.project_id = project_id or "local-offline"
        self.dataset_id = dataset_id
        self.table_id = table_id
        self.offline = offline
        self._flaky_call_count = 0

    @validate_tool_output(schema_model=DatasetMetadata, max_retries=3)
    def explore_dataset_schema(self) -> dict[str, Any]:
        """Inspect BigQuery schema (or simulated schema in --offline)."""
        if self.offline:
            cols = [
                "entity_id",
                "stock_level",
                "lead_time_days",
                "category",
                "is_critical",
            ]
        else:
            from google.cloud import bigquery

            client = bigquery.Client(project=self.project_id)
            table = client.get_table(
                f"{self.project_id}.{self.dataset_id}.{self.table_id}"
            )
            cols = [field.name for field in table.schema]
        return {
            "project_id": self.project_id,
            "dataset_id": self.dataset_id,
            "table_id": self.table_id,
            "num_columns": len(cols),
            "column_names": cols,
            "status": "ACTIVE",
        }

    @staticmethod
    def _assert_valid_observations(results: Any) -> bool:
        return isinstance(results, (list, tuple)) and len(results) > 0

    @validate_tool_output(
        schema_model=FeatureObservation,
        assertion_callback=_assert_valid_observations,
        max_retries=3,
    )
    def query_feature_observations(
        self, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Query sample feature observations validated by schema and guard."""
        return [
            {
                "entity_id": f"PART-{1000 + i}",
                "metric_value": 45.5 + i * 2.5,
                "category": "HYDRAULICS",
            }
            for i in range(limit)
        ]

    @staticmethod
    async def _async_verify_kpi_bounds(report: Any) -> bool:
        await asyncio.sleep(0.01)
        score = (
            report.get("average_score", 0.0)
            if isinstance(report, dict)
            else getattr(report, "average_score", 0.0)
        )
        return 0.0 <= score <= 100.0

    @validate_tool_output(
        schema_model=FeatureKPIReport,
        assertion_callback=_async_verify_kpi_bounds,
        max_retries=3,
    )
    async def compute_kpi_summary_async(
        self, observations: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Compute feature KPI summary with async assertion validation."""
        await asyncio.sleep(0.01)
        total = len(observations)
        avg = sum(o["metric_value"] for o in observations) / max(total, 1)
        return {
            "kpi_name": "inventory_turnover_lead_time",
            "total_samples": total,
            "average_score": round(avg, 2),
            "anomalies_detected": 0,
        }

    @validate_tool_output(
        schema_model=DriftBenchmark,
        max_retries=3,
        retry_delay_seconds=0.05,
        backoff_factor=2.0,
    )
    def fetch_model_drift_benchmark(self, metric_name: str) -> dict[str, Any]:
        """Recover from initial invalid payload using exponential backoff."""
        self._flaky_call_count += 1
        if self._flaky_call_count < 2:
            return {
                "metric": metric_name,
                "psi_score": "uncalculated",
                "is_drift_detected": False,
                "confidence": 0.9,
            }
        return {
            "metric": metric_name,
            "psi_score": 0.042,
            "is_drift_detected": False,
            "confidence": 0.98,
        }


def _assert_validation_spans(spans: list[Any]) -> None:
    span_map = {s.name: s for s in spans}
    checks = (
        ("adk_tool_1_schema_span", "explore_dataset_schema", "DatasetMetadata"),
        (
            "adk_tool_2_query_span",
            "query_feature_observations",
            "FeatureObservation",
        ),
        (
            "adk_tool_3_kpi_span",
            "compute_kpi_summary_async",
            "FeatureKPIReport",
        ),
        (
            "adk_tool_4_drift_span",
            "fetch_model_drift_benchmark",
            "DriftBenchmark",
        ),
    )
    for span_name, tool_name, schema_name in checks:
        span = span_map.get(span_name)
        assert span is not None, f"{span_name} missing"
        assert span.attributes.get("tool.validation.status") == "PASS"
        assert span.attributes.get("tool.validation.tool_name") == tool_name
        assert span.attributes.get("tool.validation.schema") == schema_name

    s4 = span_map["adk_tool_4_drift_span"]
    assert s4.attributes.get("tool.validation.attempts") == 2
    retries = [e for e in s4.events if e.name == "tool.validation.retry"]
    assert len(retries) == 1
    assert (
        retries[0].attributes.get("tool.name") == "fetch_model_drift_benchmark"
    )
    assert retries[0].attributes.get("attempt") == 1


def run_agent_validation(
    project_id: str, dataset_id: str, table_id: str, *, offline: bool = False
) -> dict[str, Any]:
    """Execute validation workflow and assert v1 OpenTelemetry attributes."""
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("adk-validation-runner")

    agent = ADKDataScienceValidationAgent(
        project_id, dataset_id, table_id, offline=offline
    )
    with tracer.start_as_current_span("adk_tool_1_schema_span"):
        schema_result = agent.explore_dataset_schema()
    with tracer.start_as_current_span("adk_tool_2_query_span"):
        observations = agent.query_feature_observations(limit=5)
    with tracer.start_as_current_span("adk_tool_3_kpi_span"):
        kpi_report = asyncio.run(agent.compute_kpi_summary_async(observations))
    with tracer.start_as_current_span("adk_tool_4_drift_span"):
        drift_report = agent.fetch_model_drift_benchmark("lead_time_days")

    spans = exporter.get_finished_spans()
    _assert_validation_spans(list(spans))
    provider.shutdown()
    return {
        "dataset_metadata": schema_result,
        "observations_retrieved": len(observations),
        "kpi_report": kpi_report,
        "drift_benchmark": drift_report,
        "telemetry_spans_verified": len(spans),
        "agent_validation_passed": True,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        default=os.getenv("GOOGLE_CLOUD_PROJECT", ""),
    )
    parser.add_argument("--dataset", default="agent_feature_store")
    parser.add_argument("--table", default="customer_features")
    parser.add_argument("--offline", action="store_true")
    return parser.parse_args()


def main() -> None:
    """Run ADK validation workflow."""
    args = _parse_args()
    results = run_agent_validation(
        args.project, args.dataset, args.table, offline=args.offline
    )
    print(json.dumps(results, indent=2))
    logger.info("ADK Tool Validation Workflow completed with 0 errors.")


if __name__ == "__main__":
    main()
