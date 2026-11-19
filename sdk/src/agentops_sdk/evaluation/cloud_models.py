# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Frozen Pydantic v2 DTOs for GenAI Evals, simulation, and loss analysis."""

from __future__ import annotations

from typing import Any

import pydantic
from typing_extensions import override

from agentops_sdk.evaluation import models

_VERTEX_EXPERIMENTS_URL = (
    "https://console.cloud.google.com/vertex-ai/experiments/locations"
)


def _attr_str(obj: Any, name: str, default: str = "") -> str:
    return str(getattr(obj, name, None) or default)


def _parse_loss_cluster(cluster: Any, total_items: int) -> LossCluster:
    taxonomy = getattr(cluster, "taxonomy_entry", None)
    count = int(getattr(cluster, "item_count", 0) or 0)
    raw_examples = getattr(cluster, "examples", None) or ()
    examples = [
        item.model_dump()
        if isinstance(item, pydantic.BaseModel)
        else {"value": str(item)}
        for item in raw_examples
    ]
    return LossCluster(
        cluster_id=_attr_str(cluster, "cluster_id", "unknown"),
        l1_category=_attr_str(taxonomy, "l1_category"),
        l2_category=_attr_str(taxonomy, "l2_category"),
        description=_attr_str(taxonomy, "description"),
        item_count=count,
        percentage=round(count / total_items * 100.0, 2)
        if total_items > 0
        else 0.0,
        examples=examples,
    )


def _parse_clusters(results: Any) -> tuple[list[LossCluster], int]:
    raw_clusters: list[Any] = []
    for result_entry in results or ():
        for cluster in getattr(result_entry, "clusters", None) or ():
            raw_clusters.append(cluster)
    total = sum(int(getattr(c, "item_count", 0) or 0) for c in raw_clusters)
    return [_parse_loss_cluster(c, total) for c in raw_clusters], total


class UserScenario(pydantic.BaseModel):
    """User conversation scenario for multi-turn agent simulation."""

    model_config = pydantic.ConfigDict(frozen=True)

    scenario_id: str
    starting_prompt: str
    conversation_plan: str = ""
    test_case_title: str = ""
    goal: str = ""
    context: str | None = None

    @override
    def model_post_init(self, context: Any) -> None:
        del context
        if not self.goal and self.conversation_plan:
            object.__setattr__(self, "goal", self.conversation_plan)
        elif not self.conversation_plan and self.goal:
            object.__setattr__(self, "conversation_plan", self.goal)

    @classmethod
    def from_eval_cases(
        cls, dataset: Any, context: str | None
    ) -> list[UserScenario]:
        """Parses Vertex AI evaluation cases into UserScenario instances."""
        scenarios: list[UserScenario] = []
        for idx, case in enumerate(getattr(dataset, "eval_cases", None) or ()):
            scenario = getattr(case, "user_scenario", None)
            if scenario is None:
                continue
            scenarios.append(
                cls(
                    scenario_id=_attr_str(
                        case, "eval_case_id", f"sc-{idx + 1}"
                    ),
                    starting_prompt=_attr_str(scenario, "starting_prompt"),
                    conversation_plan=_attr_str(scenario, "conversation_plan"),
                    test_case_title=_attr_str(scenario, "test_case_title"),
                    context=context,
                )
            )
        return scenarios


class UserScenarioGenerationConfig(pydantic.BaseModel):
    """Configuration for Vertex AI conversation scenario generation."""

    model_config = pydantic.ConfigDict(frozen=True)

    count: int = pydantic.Field(default=5, ge=1, le=100)
    model_name: str | None = None
    generation_instruction: str | None = None
    simulation_instruction: str | None = None
    environment_context: str | None = None
    environment_data: str | None = None
    allow_cross_region_model: bool | None = None


class UserSimulatorConfig(pydantic.BaseModel):
    """Configuration for multi-turn LLM-powered user simulation."""

    model_config = pydantic.ConfigDict(frozen=True)

    max_turns: int = pydantic.Field(default=5, ge=1, le=50)
    model_name: str = "gemini-2.5-flash"
    simulation_instruction: str | None = None
    temperature: float | None = None


class SimulationTrajectory(pydantic.BaseModel):
    """Recorded multi-turn dialogue trajectory from simulation."""

    model_config = pydantic.ConfigDict(frozen=True)

    scenario_id: str
    trajectory: list[dict[str, Any]] = pydantic.Field(default_factory=list)
    completed: bool = True
    turns_count: int = 0
    duration_seconds: float = 0.0
    evaluation_result: models.EvaluationResult | None = None


class LossCluster(pydantic.BaseModel):
    """Semantic failure cluster returned by Vertex AI auto-loss analysis."""

    model_config = pydantic.ConfigDict(frozen=True)

    cluster_id: str
    l1_category: str = ""
    l2_category: str = ""
    description: str = ""
    item_count: int = 0
    percentage: float = pydantic.Field(default=0.0, ge=0.0, le=100.0)
    examples: list[dict[str, Any]] = pydantic.Field(default_factory=list)


class AutoLossAnalysisResult(pydantic.BaseModel):
    """Aggregated semantic loss clustering report."""

    model_config = pydantic.ConfigDict(frozen=True)

    metric_name: str
    total_failures: int = 0
    clusters: list[LossCluster] = pydantic.Field(default_factory=list)
    analysis_time: str | None = None

    @classmethod
    def from_cloud_response(
        cls, response: Any, metric_name: str
    ) -> AutoLossAnalysisResult:
        """Builds AutoLossAnalysisResult from a GenAI Evals response."""
        results = getattr(response, "results", None) or ()
        clusters, total = _parse_clusters(results)
        timestamps = [
            ts
            for entry in results
            if (ts := getattr(entry, "analysis_time", None))
        ]
        analysis_time = getattr(response, "analysis_time", None) or (
            timestamps[0] if timestamps else None
        )
        return cls(
            metric_name=metric_name,
            total_failures=total,
            clusters=clusters,
            analysis_time=analysis_time,
        )


class RegisteredMetricInfo(pydantic.BaseModel):
    """Metadata for a custom metric in the Vertex AI Metric Registry."""

    model_config = pydantic.ConfigDict(frozen=True)

    resource_name: str
    metric_name: str
    metric_type: str
    description: str | None = None
    create_time: str | None = None

    @classmethod
    def from_cloud(
        cls, item: Any, fallback_name: str = ""
    ) -> RegisteredMetricInfo:
        """Builds RegisteredMetricInfo from a Vertex EvaluationMetric."""
        resource_name = _attr_str(item, "name", fallback_name)
        default_display = resource_name.rsplit("/", 1)[-1]
        return cls(
            resource_name=resource_name,
            metric_name=_attr_str(item, "display_name", default_display),
            metric_type="vertex_ai",
            description=getattr(item, "description", None),
        )


class CloudEvaluationRunResult(pydantic.BaseModel):
    """Outcome of a managed Vertex AI Agent Engine evaluation run."""

    model_config = pydantic.ConfigDict(frozen=True)

    run_name: str
    state: str
    metrics_summary: dict[str, Any] = pydantic.Field(default_factory=dict)
    total_items: int = 0
    failed_items: int = 0
    console_url: str = ""
    loss_clusters: list[LossCluster] = pydantic.Field(default_factory=list)

    @classmethod
    def from_cloud_run(
        cls,
        run: Any,
        project_id: str,
        location: str,
        *,
        experiment: str | None = None,
    ) -> CloudEvaluationRunResult:
        """Builds CloudEvaluationRunResult from a Vertex EvaluationRun."""
        run_name = _attr_str(run, "name")
        raw_state = getattr(run, "state", "PENDING")
        run_results = getattr(run, "evaluation_run_results", None)
        summary = getattr(run_results, "summary_metrics", None)
        loss_results = getattr(run_results, "loss_analysis_results", None)
        raw_exp = experiment or getattr(run, "evaluation_experiment", None)
        exp_id = str(raw_exp).rsplit("/", 1)[-1] if raw_exp else ""
        run_id = run_name.rsplit("/", 1)[-1]
        explicit_url = _attr_str(run, "console_url")
        console_url = explicit_url or (
            f"{_VERTEX_EXPERIMENTS_URL}/{location}/experiments/{exp_id}"
            f"/runs/{run_id}?project={project_id}"
            if exp_id and run_id and project_id and location
            else ""
        )
        return cls(
            run_name=run_name,
            state=str(getattr(raw_state, "value", raw_state)),
            metrics_summary=dict(getattr(summary, "metrics", None) or {}),
            total_items=int(getattr(summary, "total_items", 0) or 0),
            failed_items=int(getattr(summary, "failed_items", 0) or 0),
            console_url=console_url,
            loss_clusters=(
                _parse_clusters(loss_results)[0] if loss_results else []
            ),
        )
