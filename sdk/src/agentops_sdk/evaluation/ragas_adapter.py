# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Fail-closed RAGAS evaluation adapter implementing EvaluatorPort."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
import math
from typing import Any

from typing_extensions import override

from agentops_sdk.domain import exceptions
from agentops_sdk.evaluation import heuristics
from agentops_sdk.ports import evaluation as eval_ports


def _extract_scores(raw_result: Any) -> dict[str, float]:
    """Extracts finite float metric scores from a ragas evaluate() result."""
    items: Mapping[str, Any]
    if isinstance(raw_result, Mapping):
        items = raw_result
    else:
        to_df = getattr(raw_result, "to_pandas", None)
        if not callable(to_df):
            raise exceptions.EvaluationError(
                f"Unsupported RAGAS result type: {type(raw_result).__name__}"
            )
        df = to_df()
        num_cols = df.select_dtypes(include="number")
        items = {col: float(num_cols[col].mean()) for col in num_cols.columns}
    if not items:
        raise exceptions.EvaluationError("RAGAS evaluation returned no scores.")
    scores: dict[str, float] = {}
    for key, val in items.items():
        num = float(val)
        if math.isnan(num) or math.isinf(num):
            raise exceptions.EvaluationError(
                f"RAGAS metric {key!r} returned non-finite score: {val!r}"
            )
        scores[str(key)] = num
    return scores


def _trajectory_to_ragas_item(
    trajectory: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    base = heuristics.trajectory_to_item(trajectory)
    tool_ctx: list[str] = []
    for turn in trajectory:
        if str(turn.get("role", "")).lower().strip() == "tool":
            content = str(turn.get("content", "")).strip()
            if content:
                tool_ctx.append(content)
    return {
        "prompt": base["prompt"],
        "context": "\n".join(tool_ctx),
        "response": base["response"],
    }


class RagasEvaluator(eval_ports.EvaluatorPort):
    """Fail-closed adapter around ragas.evaluate() for EvaluatorPort."""

    def __init__(
        self,
        ragas_metrics: Sequence[Any] | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        self.ragas_metrics = list(ragas_metrics) if ragas_metrics else None
        self._client = client
        self.provider_name = "ragas"

    def _resolve_metrics(self, metrics: Sequence[str] | None) -> list[Any]:
        if self.ragas_metrics is not None:
            active = list(self.ragas_metrics)
            if metrics is not None:
                wanted = set(metrics)
                active = [
                    m for m in active if getattr(m, "name", str(m)) in wanted
                ]
            if not active:
                raise exceptions.EvaluationError(
                    "No matching RAGAS metrics configured."
                )
            return active
        ragas_metrics_mod: Any = getattr(self._client, "metrics", None)
        if ragas_metrics_mod is None:
            try:
                from ragas import metrics as imported_metrics

                ragas_metrics_mod = imported_metrics
            except ImportError as exc:
                raise exceptions.EvaluationError(
                    "ragas package is required for RagasEvaluator."
                ) from exc
        default_map = {
            "faithfulness": getattr(ragas_metrics_mod, "faithfulness", None),
            "answer_relevancy": getattr(
                ragas_metrics_mod, "answer_relevancy", None
            ),
        }
        chosen = (
            [default_map.get(name) for name in metrics]
            if metrics is not None
            else list(default_map.values())
        )
        resolved = [m for m in chosen if m is not None]
        if not resolved:
            raise exceptions.EvaluationError(
                f"Requested metrics {metrics!r} unavailable in ragas.metrics."
            )
        return resolved

    def _load_backends(self) -> tuple[Any, Any]:
        if self._client is not None:
            eval_fn = getattr(self._client, "evaluate", self._client)
            ds_cls = getattr(self._client, "Dataset", None)
            if ds_cls is None:
                try:
                    import datasets as hf_datasets

                    ds_cls = hf_datasets.Dataset
                except ImportError:
                    ds_cls = None
            return eval_fn, ds_cls
        try:
            import datasets as hf_datasets
            import ragas
        except ImportError as exc:
            raise exceptions.EvaluationError(
                "RAGAS dependencies (ragas, datasets) are not installed."
            ) from exc
        return ragas.evaluate, hf_datasets.Dataset

    @override
    def evaluate(
        self,
        dataset: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        if not dataset:
            raise exceptions.EvaluationError(
                "Evaluation dataset must not be empty."
            )
        eval_fn, dataset_cls = self._load_backends()
        active_metrics = self._resolve_metrics(metrics)
        payload: dict[str, Any] = {
            "question": [str(item.get("prompt", "")) for item in dataset],
            "contexts": [[str(item.get("context", ""))] for item in dataset],
            "answer": [str(item.get("response", "")) for item in dataset],
        }
        refs = [str(item.get("reference", "")) for item in dataset]
        if any(refs):
            payload["ground_truth"] = refs
        try:
            hf_dataset = (
                dataset_cls.from_dict(payload)
                if dataset_cls is not None
                else payload
            )
            result = eval_fn(hf_dataset, metrics=active_metrics)
        except exceptions.EvaluationError:
            raise
        except Exception as exc:
            raise exceptions.EvaluationError(
                f"RAGAS evaluation failed: {exc}"
            ) from exc
        return _extract_scores(result)

    @override
    def evaluate_trajectory(
        self,
        trajectory: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        return self.evaluate(
            [_trajectory_to_ragas_item(trajectory)], metrics=metrics
        )

    @override
    async def aevaluate(
        self,
        dataset: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        return await asyncio.to_thread(self.evaluate, dataset, metrics=metrics)
