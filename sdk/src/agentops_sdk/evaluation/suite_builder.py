# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Suite score normalization, pandas stats extraction, and result builder."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
import datetime
import math
from typing import Any

from agentops_sdk.domain import exceptions
from agentops_sdk.evaluation import models


@dataclasses.dataclass(frozen=True, slots=True)
class _SuiteContext:
    raw_summary: Mapping[str, float]
    table: Any
    explanations: Mapping[str, list[str]]
    normalize: bool
    thresholds: Mapping[str, float] | None


def normalize_score(value: float, enabled: bool = True) -> float:
    """Normalizes 1..5 Likert judge scores onto the unit [0.0, 1.0] scale."""
    scaled = value / 5.0 if value > 1.0 else value
    return round(min(1.0, max(0.0, scaled)), 4) if enabled else value


def _lookup_summary_score(
    metric_name: str, summary_metrics: Mapping[str, Any]
) -> float | None:
    for key in (
        f"{metric_name}/mean",
        f"{metric_name}/candidate_model_win_rate",
        metric_name,
    ):
        val = summary_metrics.get(key)
        if isinstance(val, int | float) and not isinstance(val, bool):
            return float(val)
    return None


def _lookup_table_score(
    metric_name: str, table: Any, pd_mod: Any
) -> float | None:
    if table is None or table.empty:
        return None
    for col in (f"{metric_name}/score", metric_name, f"{metric_name}/mean"):
        if col in table.columns:
            series = pd_mod.to_numeric(table[col], errors="coerce").dropna()
            if not series.empty:
                return float(series.mean())
    pairwise_col = f"{metric_name}/pairwise_choice"
    if pairwise_col in table.columns:
        return float((table[pairwise_col] == "CANDIDATE").mean())
    return None


def extract_metric_stats(
    metric_name: str,
    summary_metrics: Mapping[str, Any],
    table: Any,
    pd_mod: Any,
    normalize: bool,
) -> tuple[float, float, list[str]]:
    """Extracts normalized mean, raw mean, and explanations for one metric."""
    raw = _lookup_summary_score(metric_name, summary_metrics)
    if raw is None:
        raw = _lookup_table_score(metric_name, table, pd_mod)
    if raw is None or math.isnan(raw) or math.isinf(raw):
        raise exceptions.EvaluationError(
            f"EvalTask missing/NaN score for {metric_name!r}."
        )
    expl_col = f"{metric_name}/explanation"
    has_expl = table is not None and expl_col in table.columns
    explanations = [str(val) for val in table[expl_col]] if has_expl else []
    return normalize_score(raw, normalize), raw, explanations


def _build_metric_score(
    metric_name: str,
    row_index: int,
    summary_val: float,
    ctx: _SuiteContext,
    row_scores: Mapping[str, float] | None,
) -> models.MetricScore:
    col = f"{metric_name}/score"
    tbl = ctx.table
    has_col = tbl is not None and col in tbl.columns and row_index < len(tbl)
    fallback = (
        row_scores[metric_name]
        if row_scores and metric_name in row_scores
        else None
    )
    if has_col:
        raw_val = float(tbl[col].iloc[row_index])
        norm_val = normalize_score(raw_val) if ctx.normalize else raw_val
    else:
        raw_val = (
            ctx.raw_summary.get(metric_name, summary_val)
            if fallback is None
            else fallback
        )
        norm_val = summary_val if fallback is None else fallback
    threshold = ctx.thresholds.get(metric_name) if ctx.thresholds else None
    expl_list = ctx.explanations.get(metric_name, [])
    return models.MetricScore(
        name=metric_name,
        raw_score=raw_val,
        normalized_score=min(1.0, max(0.0, norm_val)),
        explanation=expl_list[row_index]
        if row_index < len(expl_list)
        else None,
        threshold=threshold,
        is_passing=True if threshold is None else norm_val >= threshold,
    )


def _build_instance_eval(
    idx: int,
    item: Mapping[str, Any],
    summary: Mapping[str, float],
    ctx: _SuiteContext,
    row_scores: Mapping[str, float] | None,
) -> models.InstanceEvaluation:
    row_metrics = {
        name: _build_metric_score(name, idx, val, ctx, row_scores)
        for name, val in summary.items()
    }
    ref = item.get("reference")
    return models.InstanceEvaluation(
        prompt=str(item.get("prompt", "")),
        context=str(item.get("context", "")),
        response=str(item.get("response", "")),
        reference=str(ref) if ref else None,
        metrics=row_metrics,
        is_passing=all(m.is_passing for m in row_metrics.values()),
    )


def build_suite_result(
    dataset: Sequence[Mapping[str, Any]],
    summary: Mapping[str, float],
    raw_summary: Mapping[str, float],
    *,
    per_item_scores: Sequence[Mapping[str, float]] | None = None,
    table: Any = None,
    expls: Mapping[str, list[str]] | None = None,
    normalize: bool = True,
    experiment: str = "in-memory",
    run_name: str | None = None,
    console_url: str | None = None,
    thresholds: Mapping[str, float] | None = None,
) -> models.EvaluationSuiteResult:
    """Builds EvaluationSuiteResult from summary and per-instance scores."""
    ts = datetime.datetime.now(datetime.UTC).strftime("run-%Y%m%d-%H%M%S")
    ctx = _SuiteContext(raw_summary, table, expls or {}, normalize, thresholds)
    instances = [
        _build_instance_eval(
            idx,
            item,
            summary,
            ctx,
            per_item_scores[idx] if per_item_scores else None,
        )
        for idx, item in enumerate(dataset)
    ]
    passed_count = sum(1 for inst in instances if inst.is_passing)
    return models.EvaluationSuiteResult(
        experiment=experiment,
        experiment_run_name=run_name or ts,
        console_url=console_url,
        summary_scores=dict(summary),
        raw_summary_scores=dict(raw_summary),
        instances=instances,
        total_instances=len(instances),
        passed_instances=passed_count,
        all_passed=passed_count == len(instances),
    )
