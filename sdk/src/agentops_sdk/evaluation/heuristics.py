# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Deterministic offline heuristic evaluator."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
import difflib
import json
import re
from typing import Any, TypeAlias

from typing_extensions import override

from agentops_sdk.domain import exceptions
from agentops_sdk.evaluation import registry
from agentops_sdk.ports import evaluation as eval_ports

MAX_SIMILARITY_CHARS = 20_000
MAX_REGEX_PATTERN_LEN = 500
_WORD_RE = re.compile(r"\b\w{3,}\b")
_HeuristicContext: TypeAlias = tuple[tuple[str, ...], re.Pattern[str] | None]
_ScorerFn: TypeAlias = Callable[[Mapping[str, Any], _HeuristicContext], float]

CompositeEvaluator = registry.CompositeEvaluator


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    for prefix in ("```json", "```"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    return cleaned[:-3].strip() if cleaned.endswith("```") else cleaned


def _keyword_coverage(
    item: Mapping[str, Any], context: _HeuristicContext
) -> float:
    response = str(item.get("response", "")).lower()
    targets = context[0] or tuple(
        word.lower()
        for word in _WORD_RE.findall(str(item.get("reference", "")))
    )
    if not targets:
        return 0.0
    hits = sum(1 for keyword in targets if keyword.lower() in response)
    return round(hits / len(targets), 4)


def _sequence_similarity(
    item: Mapping[str, Any], context: _HeuristicContext
) -> float:
    del context
    response = (
        str(item.get("response", "")).strip()[:MAX_SIMILARITY_CHARS].lower()
    )
    reference = (
        str(item.get("reference", "")).strip()[:MAX_SIMILARITY_CHARS].lower()
    )
    if not reference:
        return 0.0
    return round(difflib.SequenceMatcher(None, response, reference).ratio(), 4)


def _json_validity(
    item: Mapping[str, Any], context: _HeuristicContext
) -> float:
    del context
    try:
        json.loads(_strip_code_fence(str(item.get("response", ""))))
        return 1.0
    except ValueError:
        return 0.0


def _length_ratio(item: Mapping[str, Any], context: _HeuristicContext) -> float:
    del context
    response = str(item.get("response", "")).strip()
    reference = str(item.get("reference", "")).strip()
    if not reference:
        return 1.0 if response else 0.0
    return round(
        min(len(response), len(reference)) / max(len(response), len(reference)),
        4,
    )


def _exact_match(item: Mapping[str, Any], context: _HeuristicContext) -> float:
    del context
    response = str(item.get("response", "")).strip().lower()
    reference = str(item.get("reference", "")).strip().lower()
    return 1.0 if response == reference else 0.0


def _regex_match(item: Mapping[str, Any], context: _HeuristicContext) -> float:
    pattern = context[1]
    text = str(item.get("response", ""))[:MAX_SIMILARITY_CHARS]
    return 1.0 if pattern is None or pattern.search(text) else 0.0


_SCORER_DISPATCH: dict[str, _ScorerFn] = {
    "exact_match": _exact_match,
    "exact": _exact_match,
    "keyword_coverage": _keyword_coverage,
    "contains_all": _keyword_coverage,
    "keyword_overlap": _keyword_coverage,
    "similarity": _sequence_similarity,
    "levenshtein_similarity": _sequence_similarity,
    "sequence_similarity": _sequence_similarity,
    "json_validity": _json_validity,
    "is_json": _json_validity,
    "valid_json": _json_validity,
    "length_ratio": _length_ratio,
    "conciseness": _length_ratio,
    "regex_match": _regex_match,
    "pattern_match": _regex_match,
}


def _collect_role_texts(
    trajectory: Sequence[Mapping[str, Any]], roles: frozenset[str]
) -> list[str]:
    texts: list[str] = []
    for turn in trajectory:
        role = str(turn.get("role", "")).lower().strip()
        if role in roles:
            content = str(turn.get("content", "")).strip()
            if content:
                texts.append(content)
    return texts


def trajectory_to_item(
    trajectory: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Extracts first user prompt and final agent response from trajectory."""
    if not trajectory:
        raise exceptions.EvaluationError("Trajectory must not be empty.")
    prompts = _collect_role_texts(trajectory, frozenset({"user", "human"}))
    responses = _collect_role_texts(
        trajectory, frozenset({"agent", "assistant", "model"})
    )
    return {
        "prompt": prompts[0] if prompts else "",
        "response": responses[-1] if responses else "",
        "reference": "",
    }


def _compile_regex_pattern(pattern: str | None) -> re.Pattern[str] | None:
    if pattern is None:
        return None
    if len(pattern) > MAX_REGEX_PATTERN_LEN:
        raise ValueError(
            f"regex_pattern exceeds {MAX_REGEX_PATTERN_LEN} chars."
        )
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid regex_pattern: {exc}") from exc


class HeuristicEvaluator(eval_ports.EvaluatorPort):
    """Deterministic zero-dependency evaluator implementing EvaluatorPort."""

    def __init__(
        self,
        metrics: Sequence[str] | None = None,
        regex_pattern: str | None = None,
        required_keywords: Sequence[str] | None = None,
        custom_rules: Mapping[str, Callable[[Mapping[str, Any]], float]]
        | None = None,
    ) -> None:
        self.configured_metrics = tuple(
            metrics or ("exact_match", "keyword_coverage", "similarity")
        )
        self.required_keywords = tuple(required_keywords or ())
        self.custom_rules = dict(custom_rules or {})
        self.regex_pattern = regex_pattern
        self._compiled_regex = _compile_regex_pattern(regex_pattern)
        self.provider_name = "heuristic"

    @property
    def supported_metrics(self) -> list[str]:
        """Returns configured heuristic and custom rule metric names."""
        names = list(self.configured_metrics)
        for name in self.custom_rules:
            if name not in names:
                names.append(name)
        return names

    @override
    def evaluate(
        self,
        dataset: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        if not dataset:
            raise exceptions.EvaluationError("Evaluation dataset is empty.")
        active = (
            tuple(metrics)
            if metrics is not None
            else tuple(self.supported_metrics)
        )
        context: _HeuristicContext = (
            self.required_keywords,
            self._compiled_regex,
        )
        totals: dict[str, float] = {}
        for name in active:
            scorer = _SCORER_DISPATCH.get(name.lower().strip())
            custom = self.custom_rules.get(name)
            if custom is not None:
                values = [float(custom(item)) for item in dataset]
            elif scorer is not None:
                values = [scorer(item, context) for item in dataset]
            else:
                raise exceptions.EvaluationError(
                    f"Unsupported metric: {name!r}"
                )
            totals[name] = round(sum(values) / len(values), 4)
        return totals

    @override
    def evaluate_trajectory(
        self,
        trajectory: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        return self.evaluate([trajectory_to_item(trajectory)], metrics=metrics)

    @override
    async def aevaluate(
        self,
        dataset: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        return await asyncio.to_thread(self.evaluate, dataset, metrics=metrics)
