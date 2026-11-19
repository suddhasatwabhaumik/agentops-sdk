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
    mode: str,
    scores: Mapping[str, float],
    thresholds: Mapping[str, float],
    is_passing: bool,
    console_url: str,
    evaluator: eval_ports.EvaluatorPort,
) -> None:
    provider_name = str(
        getattr(evaluator, "provider_name", type(evaluator).__name__)
    )
    reporting.get_session_reporter().add_record(
        reporting.QualityGateTestRecord(
            test_name=test_name,
            eval_mode=mode,
            metrics=dict(scores),
            thresholds=dict(thresholds),
            is_passing=is_passing,
            console_url=console_url or None,
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


def _build_gate_result(
    scores: dict[str, float],
    threshold_map: Mapping[str, float],
    mode: str,
    run_name: str,
    console_url: str,
) -> models.EvaluationResult:
    failed = _check_thresholds(scores, threshold_map)
    return models.EvaluationResult(
        scores=scores,
        raw_scores=scores,
        is_passing=not failed,
        failed_metrics=failed,
        eval_mode=mode,
        run_name=run_name,
        console_url=console_url,
    )


def _evaluate_gate(
    payload: Sequence[Mapping[str, Any]],
    *,
    is_trajectory: bool,
    thresholds: Mapping[str, float] | None,
    metrics: Sequence[str] | None,
    evaluator: _EvaluatorSpec,
    ev_kwargs: Mapping[str, Any] | None,
    run_name: str,
    console_url: str,
    record_report: bool,
    test_name: str,
) -> models.EvaluationResult:
    resolved_ev, threshold_map, active = _resolve_gate_inputs(
        evaluator, ev_kwargs, thresholds, metrics, is_trajectory=is_trajectory
    )
    scores = (
        resolved_ev.evaluate_trajectory(payload, metrics=active)
        if is_trajectory
        else resolved_ev.evaluate(payload, metrics=active)
    )
    mode = "trajectory" if is_trajectory else "pointwise"
    result = _build_gate_result(
        scores, threshold_map, mode, run_name, console_url
    )
    if record_report:
        _emit_record(
            test_name,
            mode,
            scores,
            threshold_map,
            result.is_passing,
            console_url,
            resolved_ev,
        )
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
    return _evaluate_gate(
        rows,
        is_trajectory=False,
        thresholds=thresholds,
        metrics=metrics,
        evaluator=evaluator,
        ev_kwargs=evaluator_kwargs,
        run_name=run_name,
        console_url=console_url,
        record_report=record_report,
        test_name=test_name,
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
    return _evaluate_gate(
        list(trajectory),
        is_trajectory=True,
        thresholds=thresholds,
        metrics=metrics,
        evaluator=evaluator,
        ev_kwargs=evaluator_kwargs,
        run_name=run_name,
        console_url=console_url,
        record_report=record_report,
        test_name=test_name,
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
