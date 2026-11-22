# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Metric resolution and GenAI/EvalTask helpers for VertexEvalTaskAdapter."""

from __future__ import annotations

from collections.abc import Sequence
import math
from typing import Any

from vertexai._genai import types as vtypes

from agentops_sdk.domain import exceptions
from agentops_sdk.evaluation import _task_runner
from agentops_sdk.evaluation import models
from agentops_sdk.evaluation import reporting

_accepts_autorater_config = _task_runner.accepts_autorater_config
run_eval_task_instance = _task_runner.run_eval_task_instance
trajectory_to_vertex_row = _task_runner.trajectory_to_vertex_row

_RELEVANCE_ALIASES = frozenset(
    {"question_answering_relevance", "qa_relevance", "relevance"}
)
_GROUNDEDNESS_PROMPT = (
    "Evaluate whether the response is fully grounded in the provided context.\n"
    "Prompt: {prompt}\nContext: {context}\nResponse: {response}\n"
    "Rate 1 if grounded, 0 otherwise."
)
_QA_RELEVANCE_PROMPT = (
    "Evaluate how relevant and accurate the response is to the prompt.\n"
    "Prompt: {prompt}\nResponse: {response}\n"
    "Rate from 1 (irrelevant) to 5 (highly relevant and accurate)."
)


def is_metric_uri(metric: Any) -> bool:
    """Returns True if metric is a Vertex AI Metric Registry resource URI."""
    raw = metric if isinstance(metric, str) else ""
    return raw.startswith("projects/") and "/evaluationMetrics/" in raw


def metric_key(metric: Any) -> str:
    """Returns the canonical display key for a metric specifier."""
    if isinstance(metric, models.CustomRubricMetric):
        return metric.name
    if isinstance(metric, str):
        return (
            metric.rsplit("/", 1)[-1]
            if is_metric_uri(metric)
            else metric.split(":", 1)[0]
        )
    return str(
        getattr(metric, "metric_name", None)
        or getattr(metric, "metric", metric)
    )


def infer_genai_location(
    metrics: Sequence[Any],
    config: models.VertexAIEvalConfig,
    default_location: str,
) -> str:
    """Infers regional location from URIs when judge_location is default."""
    if config.judge_location != "global":
        return config.judge_location
    for metric in metrics:
        if is_metric_uri(metric):
            parts = str(metric).split("/")
            if len(parts) >= 4 and parts[2] == "locations" and parts[3]:
                return parts[3]
    return config.judge_location or default_location


def _resolve_custom_task_metric(
    metric: models.CustomRubricMetric, *, has_context: bool, eval_mod: Any
) -> Any:
    input_vars = [
        "prompt",
        "response",
        *(["context"] if has_context else ()),
    ]
    rubric = {str(k): str(v) for k, v in sorted(metric.rubric.items())}
    assembled = str(
        eval_mod.PointwiseMetricPromptTemplate(
            criteria={"criteria": metric.criteria},
            rating_rubric=rubric,
            input_variables=input_vars,
        ).assemble()
    )
    return eval_mod.PointwiseMetric(
        metric=metric.name, metric_prompt_template=assembled
    )


def _resolve_string_task_metric(
    metric: str, *, has_context: bool, eval_mod: Any, pm_mod: Any
) -> Any:
    templates = eval_mod.MetricPromptTemplateExamples
    clean = metric.strip()
    lowered = clean.lower()
    if lowered.startswith("trajectory_single_tool_use:"):
        return pm_mod.TrajectorySingleToolUse(
            tool_name=clean.split(":", 1)[1].strip()
        )
    if lowered == "groundedness" and has_context:
        assembled = str(
            eval_mod.PointwiseMetricPromptTemplate(
                criteria={"Groundedness": "Grounded in context."},
                rating_rubric={"1": "Yes.", "0": "No."},
                input_variables=["prompt", "response", "context"],
            ).assemble()
        )
        return eval_mod.PointwiseMetric(
            metric="groundedness", metric_prompt_template=assembled
        )
    if lowered in _RELEVANCE_ALIASES:
        qa_template = templates.Pointwise.QUESTION_ANSWERING_QUALITY
        return eval_mod.PointwiseMetric(
            metric=clean,
            metric_prompt_template=qa_template.metric_prompt_template,
        )
    if (
        pointwise := getattr(templates.Pointwise, lowered.upper(), None)
    ) is not None:
        return pointwise
    if lowered.startswith("pairwise_"):
        return getattr(templates.Pairwise, lowered[9:].upper(), clean)
    return clean


def resolve_eval_task_metric(
    metric: Any,
    *,
    has_context: bool,
    eval_mod: Any,
    pm_mod: Any,
) -> Any:
    """Resolves a metric specifier for classic vertexai.evaluation.EvalTask."""
    if isinstance(metric, models.CustomRubricMetric):
        return _resolve_custom_task_metric(
            metric, has_context=has_context, eval_mod=eval_mod
        )
    if isinstance(metric, str):
        return _resolve_string_task_metric(
            metric, has_context=has_context, eval_mod=eval_mod, pm_mod=pm_mod
        )
    return metric


def _build_rubric_llm_metric(
    metric: models.CustomRubricMetric,
) -> vtypes.LLMMetric:
    rubric_lines = "\n".join(
        f"{score}: {desc}" for score, desc in sorted(metric.rubric.items())
    )
    template = (
        f"Criteria: {metric.criteria}\nRubric:\n{rubric_lines}\n"
        "Prompt: {prompt}\nResponse: {response}"
    )
    return vtypes.LLMMetric(name=metric.name, prompt_template=template)


def resolve_genai_metric(metric: Any, *, has_context: bool) -> Any:
    """Resolves URI, rubric, or predefined metric for GenAI Evals."""
    if isinstance(metric, vtypes.Metric):
        return metric
    if hasattr(metric, "resolve") and hasattr(metric, "name"):
        return metric
    if is_metric_uri(metric):
        return vtypes.Metric(
            name=metric_key(metric), metric_resource_name=str(metric)
        )
    if isinstance(metric, models.CustomRubricMetric):
        return _build_rubric_llm_metric(metric)
    if isinstance(metric, str):
        clean = metric.strip()
        lowered = clean.lower()
        if lowered == "groundedness" and has_context:
            return vtypes.LLMMetric(
                name="groundedness", prompt_template=_GROUNDEDNESS_PROMPT
            )
        if lowered in _RELEVANCE_ALIASES:
            return vtypes.LLMMetric(
                name=clean, prompt_template=_QA_RELEVANCE_PROMPT
            )
        if (
            prebuilt := getattr(vtypes.RubricMetric, clean.upper(), None)
        ) is not None:
            return prebuilt
        return vtypes.Metric(name=clean)
    return metric


def extract_genai_case_results(
    dataset_df: Any, response: Any
) -> tuple[Any, dict[str, list[str]]]:
    """Extracts per-case metric scores and explanations from GenAI results."""
    table = dataset_df.copy()
    explanations: dict[str, list[str]] = {}
    case_results = getattr(response, "eval_case_results", None) or ()
    for pos, case_res in enumerate(case_results):
        row_idx = getattr(case_res, "eval_case_index", None)
        target_idx = pos if row_idx is None else int(row_idx)
        candidates = getattr(case_res, "response_candidate_results", None) or ()
        if not candidates or target_idx >= len(table):
            continue
        metric_results = getattr(candidates[0], "metric_results", None) or {}
        for name, m_res in metric_results.items():
            score = getattr(m_res, "score", None)
            if isinstance(score, int | float) and not isinstance(score, bool):
                table.loc[target_idx, f"{name}/score"] = float(score)
            expl = getattr(m_res, "explanation", None)
            if expl is not None:
                text = str(expl)
                table.loc[target_idx, f"{name}/explanation"] = text
                explanations.setdefault(name, []).append(text)
    return table, explanations


def collect_task_stats(
    active: Sequence[Any], run: Any, pd_mod: Any, normalize: bool
) -> tuple[dict[str, float], dict[str, float], dict[str, list[str]], Any]:
    """Extracts summary scores and per-item stats from EvalTask outputs."""
    table = getattr(run, "metrics_table", None)
    summary = getattr(run, "summary_metrics", None) or {}
    if (table is None or table.empty) and not summary:
        raise exceptions.EvaluationError("EvalTask returned empty results.")
    scores: dict[str, float] = {}
    raw_scores: dict[str, float] = {}
    explanations: dict[str, list[str]] = {}
    for metric in active:
        key = metric_key(metric)
        norm_val, raw_val, expl_list = reporting.extract_metric_stats(
            key, summary, table, pd_mod, normalize
        )
        scores[key] = norm_val
        raw_scores[key] = raw_val
        if expl_list:
            explanations[key] = expl_list
    return scores, raw_scores, explanations, table


def collect_genai_summary(
    response: Any,
    requested_metrics: Sequence[Any],
    table: Any,
    pd_mod: Any,
    normalize: bool,
) -> tuple[dict[str, float], dict[str, float]]:
    """Extracts summary and fallback per-case scores for GenAI evals."""
    raw_by_lower: dict[str, float] = {}
    for item in getattr(response, "summary_metrics", None) or ():
        name = str(getattr(item, "metric_name", "") or "")
        val = getattr(item, "mean_score", None)
        if name and val is not None and not math.isnan(float(val)):
            raw_by_lower[name.lower()] = float(val)
    scores: dict[str, float] = {}
    raw_scores: dict[str, float] = {}
    for metric in requested_metrics:
        key = metric_key(metric)
        raw_val = raw_by_lower.get(key.lower())
        if raw_val is None:
            norm_val, raw_val, _ = reporting.extract_metric_stats(
                key, {}, table, pd_mod, normalize
            )
            scores[key] = norm_val
            raw_scores[key] = raw_val
        else:
            raw_scores[key] = raw_val
            scores[key] = reporting.normalize_score(raw_val, normalize)
    return scores, raw_scores
