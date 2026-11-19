# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Deterministic InMemoryEvaluator for offline tests without cloud calls."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
import re
from typing import Any

from typing_extensions import override

from agentops_sdk.domain import exceptions
from agentops_sdk.evaluation import models
from agentops_sdk.evaluation import registry
from agentops_sdk.evaluation import reporting
from agentops_sdk.ports import evaluation as eval_ports

EvaluatorRegistry = registry.EvaluatorRegistry
evaluator_registry = registry.evaluator_registry
get_evaluator = registry.get_evaluator

_STOP_WORDS_TEXT = (
    "what is the a an are of for to in on at by with and or do does how "
    "why where when which can could should would be been have has had it "
    "its this that these those"
)
_STOP_WORDS = frozenset(re.findall(r"[a-z]+", _STOP_WORDS_TEXT))
_RELEVANCE_METRIC_ALIASES = frozenset(
    {
        "question_answering_relevance",
        "question_answering_quality",
        "qa_relevance",
        "relevance",
    }
)
_ERROR_MARKERS = (
    "internal error",
    "sorry",
    "cannot process",
    "exception",
    "failed",
)


def _extract_keywords(text: str) -> set[str]:
    words = re.findall(r"\w+", text.lower())
    return {w for w in words if w not in _STOP_WORDS and len(w) > 1} or set(
        words
    )


def _token_overlap(source: set[str], target: set[str]) -> float:
    if not source:
        return 1.0 if not target else 0.0
    hits = sum(
        1
        for token in source
        if token in target
        or (
            len(token) >= 4
            and any(len(t) >= 4 and token[:4] == t[:4] for t in target)
        )
    )
    return round(min(1.0, hits / len(source)), 4)


def _score_row(
    item: Mapping[str, Any], active: tuple[str, ...]
) -> dict[str, float]:
    response = str(item.get("response", ""))
    context = str(item.get("context", ""))
    prompt = str(item.get("prompt", ""))
    response_kws = _extract_keywords(response)
    scores: dict[str, float] = {}
    for name in active:
        lowered = name.lower().strip()
        if lowered == "groundedness":
            scores[name] = (
                _token_overlap(response_kws, _extract_keywords(context))
                if context and response
                else 0.0
            )
        elif lowered in _RELEVANCE_METRIC_ALIASES:
            scores[name] = (
                _token_overlap(_extract_keywords(prompt), response_kws)
                if prompt and response
                else 0.0
            )
    return scores


def _trajectory_scores(
    trajectory: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    has_user = any(
        str(turn.get("role", "")).lower().strip() in {"user", "human"}
        and str(turn.get("content", "")).strip()
        for turn in trajectory
    )
    has_agent = any(
        str(turn.get("role", "")).lower().strip()
        in {"agent", "assistant", "model"}
        and (str(turn.get("content", "")).strip() or turn.get("tool_calls"))
        for turn in trajectory
    )
    if not trajectory or not has_user or not has_agent:
        return dict.fromkeys(
            (
                "tool_use_quality",
                "task_success",
                "multi_turn_chat_quality",
                "multi_turn_safety",
            ),
            0.0,
        )
    used_tool = any(bool(turn.get("tool_calls")) for turn in trajectory)
    has_error = any(
        any(
            marker in str(turn.get("content", "")).lower()
            for marker in _ERROR_MARKERS
        )
        for turn in trajectory
    )
    return {
        "tool_use_quality": (0.95 if used_tool else 0.50)
        if has_error
        else (0.92 if used_tool else 0.85),
        "task_success": 0.30 if has_error else 0.90,
        "multi_turn_chat_quality": 0.40 if has_error else 0.90,
        "multi_turn_safety": 0.85 if has_error else 0.95,
    }


def _filter_scores(
    scores: Mapping[str, float], metrics: Sequence[str] | None
) -> dict[str, float]:
    if metrics is None:
        return dict(scores)
    return {key: float(scores[key]) for key in metrics if key in scores}


class InMemoryEvaluator(eval_ports.EvaluatorPort):
    """Deterministic offline evaluator for tests without cloud calls."""

    def __init__(
        self,
        *,
        default_scores: Mapping[str, float] | None = None,
        metrics: Sequence[str] | None = None,
    ) -> None:
        self._defaults = (
            dict(default_scores) if default_scores is not None else None
        )
        self._metrics = tuple(
            metrics or ("groundedness", "question_answering_relevance")
        )
        self.provider_name = "in_memory"

    @override
    def evaluate(
        self,
        dataset: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        if not dataset:
            raise exceptions.EvaluationError("Evaluation dataset is empty.")
        if self._defaults is not None:
            return _filter_scores(self._defaults, metrics)
        active = tuple(metrics) if metrics is not None else self._metrics
        sums: dict[str, list[float]] = {}
        for item in dataset:
            for key, val in _score_row(item, active).items():
                sums.setdefault(key, []).append(val)
        return {
            key: round(sum(vals) / len(vals), 4) for key, vals in sums.items()
        }

    @override
    def evaluate_trajectory(
        self,
        trajectory: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        if not trajectory:
            raise exceptions.EvaluationError(
                "Evaluation trajectory must not be empty."
            )
        if self._defaults is not None:
            return _filter_scores(self._defaults, metrics)
        return _filter_scores(_trajectory_scores(trajectory), metrics)

    @override
    async def aevaluate(
        self,
        dataset: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        return await asyncio.to_thread(self.evaluate, dataset, metrics=metrics)

    def evaluate_suite(
        self,
        dataset: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
        experiment_run_name: str | None = None,
        thresholds: Mapping[str, float] | None = None,
    ) -> models.EvaluationSuiteResult:
        """Runs deterministic evaluation and returns EvaluationSuiteResult."""
        summary = self.evaluate(dataset, metrics=metrics)
        rows = [self.evaluate([item], metrics=metrics) for item in dataset]
        return reporting.build_suite_result(
            dataset,
            summary,
            summary,
            per_item_scores=rows,
            experiment="in-memory",
            run_name=experiment_run_name,
            thresholds=thresholds,
        )
