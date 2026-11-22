# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Core frozen Pydantic v2 DTOs for pointwise, suite, and gate evaluation."""

from __future__ import annotations

from collections.abc import Callable
import os
from typing import Any, Literal

import pydantic

_DEFAULT_EVAL_METRICS = ("groundedness", "question_answering_quality")


def _default_judge_model() -> str:
    for var in ("AGENTOPS_EVAL_AUTORATER_MODEL", "AGENTOPS_EVAL_JUDGE_MODEL"):
        val = (os.getenv(var) or "").strip()
        if val:
            return val
    return "gemini-3.8-flash"


class CustomRubricMetric(pydantic.BaseModel):
    """Domain-agnostic LLM-as-a-judge rubric metric definition."""

    model_config = pydantic.ConfigDict(
        frozen=True, arbitrary_types_allowed=True
    )

    name: str
    criteria: str
    rubric: dict[int, str]
    description: str | None = None


class VertexAIEvalConfig(pydantic.BaseModel):
    """Configuration for Vertex AI EvalTask and GenAI Evals adapters."""

    model_config = pydantic.ConfigDict(
        frozen=True, arbitrary_types_allowed=True
    )

    project_id: str | None = None
    location: str | None = None
    judge_location: str = "global"
    judge_model: str = pydantic.Field(default_factory=_default_judge_model)
    experiment: str | None = None
    experiment_run_name: str | None = None
    output_uri_prefix: str | None = None
    retry_timeout: float = pydantic.Field(default=120.0, gt=0.0)
    evaluation_service_qps: float | None = None
    normalize_scores: bool = True
    eval_mode: Literal["auto", "pointwise", "agentic"] = "auto"
    metrics: list[Any] = pydantic.Field(
        default_factory=lambda: list(_DEFAULT_EVAL_METRICS)
    )
    metric_column_mapping: dict[str, str] = pydantic.Field(default_factory=dict)
    prompt_template: str | None = None
    model: str | Callable[[str], str] | None = None


class MetricScore(pydantic.BaseModel):
    """Normalized and raw evaluation score for a single metric."""

    model_config = pydantic.ConfigDict(frozen=True)

    name: str
    raw_score: float
    normalized_score: float = pydantic.Field(ge=0.0, le=1.0)
    explanation: str | None = None
    threshold: float | None = None
    is_passing: bool = True


class InstanceEvaluation(pydantic.BaseModel):
    """Per-instance evaluation breakdown across configured metrics."""

    model_config = pydantic.ConfigDict(frozen=True)

    prompt: str
    context: str = ""
    response: str
    reference: str | None = None
    metrics: dict[str, MetricScore] = pydantic.Field(default_factory=dict)
    is_passing: bool = True


class EvaluationSuiteResult(pydantic.BaseModel):
    """Aggregated evaluation suite summary and per-instance records."""

    model_config = pydantic.ConfigDict(frozen=True)

    experiment: str = ""
    experiment_run_name: str = ""
    console_url: str | None = None
    summary_scores: dict[str, float] = pydantic.Field(default_factory=dict)
    raw_summary_scores: dict[str, float] = pydantic.Field(default_factory=dict)
    instances: list[InstanceEvaluation] = pydantic.Field(default_factory=list)
    total_instances: int = 0
    passed_instances: int = 0
    all_passed: bool = True


class EvaluationResult(pydantic.BaseModel):
    """Consolidated quality gate or experiment variant evaluation outcome."""

    model_config = pydantic.ConfigDict(frozen=True)

    scores: dict[str, float] = pydantic.Field(default_factory=dict)
    raw_scores: dict[str, float] = pydantic.Field(default_factory=dict)
    is_passing: bool
    failed_metrics: dict[str, tuple[float, float]] = pydantic.Field(
        default_factory=dict
    )
    explanations: dict[str, str] = pydantic.Field(default_factory=dict)
    eval_mode: str = "pointwise"
    run_name: str = ""
    console_url: str = ""
