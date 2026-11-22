# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""CI/CD evaluation quality gates and metric-code safety checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypeAlias

from agentops_sdk.domain import exceptions
from agentops_sdk.domain import predicates
from agentops_sdk.evaluation import code_validator
from agentops_sdk.evaluation import in_memory
from agentops_sdk.evaluation import models
from agentops_sdk.evaluation import reporting
from agentops_sdk.ports import evaluation as eval_ports

_EvaluatorSpec: TypeAlias = (
    str | eval_ports.EvaluatorPort | Sequence[str | eval_ports.EvaluatorPort]
)

validate_metric_code = code_validator.validate_metric_code


def _resolve_thresholds(
    thresholds: Mapping[str, float] | None, *, is_trajectory: bool
) -> dict[str, float]:
    if thresholds is not None:
        return dict(thresholds)
    reporter = reporting.get_session_reporter()
    if is_trajectory:
        return {
            "tool_use_quality": reporter.default_tool_quality,
            "task_success": reporter.default_task_success,
        }
    return {
        "groundedness": reporter.default_groundedness,
        "question_answering_relevance": reporter.default_relevance,
    }


def _check_thresholds(
    scores: Mapping[str, float], active_thresholds: Mapping[str, float]
) -> dict[str, tuple[float, float]]:
    failed: dict[str, tuple[float, float]] = {}
    for name, threshold in active_thresholds.items():
        try:
            passed = predicates.MetricThresholdPredicate(
                name, threshold, "ge"
            ).evaluate(scores)
        except KeyError as exc:
            raise exceptions.QualityGateError(
                f"Requested metric {name!r} missing."
            ) from exc
        if not passed:
            failed[name] = (scores[name], threshold)
    return failed


def _emit_record(
    test_name: str,
    result: models.EvaluationResult,
    thresholds: Mapping[str, float],
    evaluator: eval_ports.EvaluatorPort,
) -> None:
    provider_name = str(
        getattr(evaluator, "provider_name", type(evaluator).__name__)
    )
    reporting.get_session_reporter().add_record(
        reporting.QualityGateTestRecord(
            test_name=test_name,
            eval_mode=result.eval_mode,
            metrics=dict(result.scores),
            thresholds=dict(thresholds),
            is_passing=result.is_passing,
            explanations=dict(result.explanations),
            console_url=result.console_url or None,
            provider=provider_name,
        )
    )


def _resolve_gate_inputs(
    evaluator: _EvaluatorSpec,
    ev_kwargs: Mapping[str, Any] | None,
    thresholds: Mapping[str, float] | None,
    metrics: Sequence[str] | None,
    *,
    is_trajectory: bool,
) -> tuple[eval_ports.EvaluatorPort, dict[str, float], list[str]]:
    try:
        resolved_ev = in_memory.get_evaluator(evaluator, **(ev_kwargs or {}))
    except ValueError as exc:
        raise exceptions.QualityGateError(
            f"Invalid evaluator mode: {exc}"
        ) from exc
    threshold_map = _resolve_thresholds(thresholds, is_trajectory=is_trajectory)
    active = list(metrics) if metrics is not None else list(threshold_map)
    if not active:
        raise exceptions.QualityGateError("No evaluation metrics specified.")
    return resolved_ev, threshold_map, active


def _extract_suite_explanations(
    suite: models.EvaluationSuiteResult,
) -> dict[str, str]:
    grouped: dict[str, list[str]] = {}
    for inst in suite.instances:
        for name, score in inst.metrics.items():
            if score.explanation:
                grouped.setdefault(name, []).append(score.explanation)
    return {name: "\n".join(parts) for name, parts in grouped.items()}


def _build_gate_result(
    scores: dict[str, float],
    threshold_map: Mapping[str, float],
    mode: str,
    run_name: str,
    console_url: str,
    *,
    raw_scores: dict[str, float] | None = None,
    explanations: dict[str, str] | None = None,
) -> models.EvaluationResult:
    failed = _check_thresholds(scores, threshold_map)
    return models.EvaluationResult(
        scores=scores,
        raw_scores=raw_scores if raw_scores is not None else scores,
        is_passing=not failed,
        failed_metrics=failed,
        explanations=explanations or {},
        eval_mode=mode,
        run_name=run_name,
        console_url=console_url,
    )


def _suite_gate_result(
    suite: models.EvaluationSuiteResult,
    threshold_map: Mapping[str, float],
    run_name: str,
    console_url: str,
) -> models.EvaluationResult:
    scores = dict(suite.summary_scores)
    resolved_run = run_name or (
        suite.experiment_run_name if suite.console_url else ""
    )
    return _build_gate_result(
        scores,
        threshold_map,
        "pointwise",
        resolved_run,
        console_url or suite.console_url or "",
        raw_scores=dict(suite.raw_summary_scores or scores),
        explanations=_extract_suite_explanations(suite),
    )


def _run_gate_evaluator(
    resolved_ev: eval_ports.EvaluatorPort,
    payload: Sequence[Mapping[str, Any]],
    active: Sequence[str],
    threshold_map: Mapping[str, float],
    *,
    is_trajectory: bool,
    run_name: str,
    console_url: str,
) -> models.EvaluationResult:
    suite_fn = (
        None if is_trajectory else getattr(resolved_ev, "evaluate_suite", None)
    )
    if callable(suite_fn):
        suite = suite_fn(
            payload,
            metrics=active,
            experiment_run_name=run_name or None,
            thresholds=threshold_map,
        )
        return _suite_gate_result(suite, threshold_map, run_name, console_url)
    scores = (
        resolved_ev.evaluate_trajectory(payload, metrics=active)
        if is_trajectory
        else resolved_ev.evaluate(payload, metrics=active)
    )
    mode = "trajectory" if is_trajectory else "pointwise"
    return _build_gate_result(
        scores, threshold_map, mode, run_name, console_url
    )


def _evaluate_gate(
    payload: Sequence[Mapping[str, Any]],
    is_trajectory: bool,
    thresholds: Mapping[str, float] | None,
    metrics: Sequence[str] | None,
    evaluator: _EvaluatorSpec,
    ev_kwargs: Mapping[str, Any] | None,
    opts: tuple[str, str, bool, str],
) -> models.EvaluationResult:
    run_name, console_url, record_report, test_name = opts
    resolved_ev, threshold_map, active = _resolve_gate_inputs(
        evaluator, ev_kwargs, thresholds, metrics, is_trajectory=is_trajectory
    )
    result = _run_gate_evaluator(
        resolved_ev,
        payload,
        active,
        threshold_map,
        is_trajectory=is_trajectory,
        run_name=run_name,
        console_url=console_url,
    )
    if record_report:
        _emit_record(test_name, result, threshold_map, resolved_ev)
    return result


def evaluate_agent_response(
    dataset: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    *,
    thresholds: Mapping[str, float] | None = None,
    metrics: Sequence[str] | None = None,
    evaluator: _EvaluatorSpec = "in_memory",
    evaluator_kwargs: Mapping[str, Any] | None = None,
    run_name: str = "",
    console_url: str = "",
    record_report: bool = True,
    test_name: str = "agent_response_gate",
) -> models.EvaluationResult:
    """Evaluates response dataset against quality thresholds."""
    rows = [dataset] if isinstance(dataset, Mapping) else list(dataset)
    opts = (run_name, console_url, record_report, test_name)
    return _evaluate_gate(
        rows, False, thresholds, metrics, evaluator, evaluator_kwargs, opts
    )


def evaluate_agent_trajectory(
    trajectory: Sequence[Mapping[str, Any]],
    *,
    thresholds: Mapping[str, float] | None = None,
    metrics: Sequence[str] | None = None,
    evaluator: _EvaluatorSpec = "in_memory",
    evaluator_kwargs: Mapping[str, Any] | None = None,
    run_name: str = "",
    console_url: str = "",
    record_report: bool = True,
    test_name: str = "agent_trajectory_gate",
) -> models.EvaluationResult:
    """Evaluates multi-turn agent trajectory against quality thresholds."""
    opts = (run_name, console_url, record_report, test_name)
    return _evaluate_gate(
        list(trajectory),
        True,
        thresholds,
        metrics,
        evaluator,
        evaluator_kwargs,
        opts,
    )


def _raise_if_failing(
    result: models.EvaluationResult, label: str
) -> models.EvaluationResult:
    if not result.is_passing:
        markdown = reporting.get_session_reporter().generate_markdown()
        raise exceptions.QualityGateError(
            f"{label} failed: {sorted(result.failed_metrics)}",
            failed_metrics=result.failed_metrics,
            report_markdown=markdown,
        )
    return result


def assert_quality_gate(
    dataset: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    thresholds: Mapping[str, float] | None = None,
    **kwargs: Any,
) -> models.EvaluationResult:
    """Asserts quality thresholds on dataset, raising QualityGateError."""
    kwargs.setdefault("test_name", "assert_quality_gate")
    result = evaluate_agent_response(dataset, thresholds=thresholds, **kwargs)
    return _raise_if_failing(result, "Quality gate")


def assert_trajectory_quality_gate(
    trajectory: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, float] | None = None,
    **kwargs: Any,
) -> models.EvaluationResult:
    """Asserts quality thresholds on trajectory, raising QualityGateError."""
    kwargs.setdefault("test_name", "assert_trajectory_quality_gate")
    result = evaluate_agent_trajectory(
        trajectory, thresholds=thresholds, **kwargs
    )
    return _raise_if_failing(result, "Trajectory gate")
