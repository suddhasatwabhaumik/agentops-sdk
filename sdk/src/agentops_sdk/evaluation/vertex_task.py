# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Vertex AI EvalTask adapter satisfying EvaluatorPort."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
import datetime
import importlib
from typing import Any, TypeAlias

from google.cloud import aiplatform
from typing_extensions import override
import vertexai

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.evaluation import _vertex_metrics
from agentops_sdk.evaluation import heuristics
from agentops_sdk.evaluation import models
from agentops_sdk.evaluation import reporting
from agentops_sdk.ports import evaluation as eval_ports

_DetailedEvalOutput: TypeAlias = tuple[
    dict[str, float], dict[str, float], dict[str, list[str]], Any, str
]
_TRAJECTORY_DEFAULT_METRICS = ("multi_turn_chat_quality", "multi_turn_safety")
_CONSOLE_EXPERIMENTS_URL = (
    "https://console.cloud.google.com/vertex-ai/experiments/locations"
)
_LAZY_SUBMODULES = {
    "evaluation": "vertexai.evaluation",
    "pm": "vertexai.preview.evaluation.metrics",
}


def __getattr__(name: str) -> Any:
    if (mod_path := _LAZY_SUBMODULES.get(name)) is not None:
        try:
            loaded = importlib.import_module(mod_path)
        except ImportError as exc:
            raise exceptions.EvaluationError(
                "Vertex AI EvalTask requires optional dependencies; "
                "install agentops-sdk[eval]."
            ) from exc
        globals()[name] = loaded
        return loaded
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _get_lazy_module(name: str) -> Any:
    return globals().get(name) or __getattr__(name)


def _trajectory_to_vertex_row(
    trajectory: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    base = heuristics.trajectory_to_item(trajectory)
    history = [
        f"{t.get('role', '')}: {c}"
        for t in trajectory
        if (c := t.get("content"))
    ]
    calls: list[dict[str, Any]] = []
    for turn in trajectory:
        for tc in turn.get("tool_calls") or ():
            if isinstance(tc, Mapping):
                calls.append(
                    {
                        "tool_name": str(tc.get("name", "")),
                        "tool_input": dict(tc.get("args") or {}),
                    }
                )
    return {
        "prompt": base["prompt"],
        "response": base["response"],
        "history": "\n".join(history[:-1]),
        "conversation_history": list(trajectory),
        "predicted_trajectory": calls,
    }


class VertexEvalTaskAdapter(eval_ports.EvaluatorPort):
    """Vertex AI EvalTask and GenAI Evals adapter."""

    def __init__(
        self,
        config: models.VertexAIEvalConfig | None = None,
        *,
        client: Any | None = None,
        eval_task_factory: Callable[..., Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._config = config or models.VertexAIEvalConfig(**kwargs)
        self._client = client
        self._eval_task_factory = eval_task_factory
        self.provider_name = "vertex_ai"

    def _prepare_context(
        self,
        dataset: Sequence[Mapping[str, Any]],
        metrics: Sequence[Any] | None,
    ) -> tuple[Any, list[Any], str, str]:
        if not dataset:
            raise exceptions.EvaluationError("Evaluation dataset is empty.")
        try:
            import pandas as pd_mod
        except ImportError as exc:
            raise exceptions.EvaluationError(
                "pandas is required for VertexEvalTaskAdapter; "
                "install agentops-sdk[eval]."
            ) from exc
        active = list(metrics if metrics is not None else self._config.metrics)
        if not active:
            raise exceptions.EvaluationError("Metrics list must not be empty.")
        try:
            project_id, location = settings.resolve_gcp_project_and_location(
                self._config.project_id,
                self._config.location,
                require_project=True,
            )
        except exceptions.ConfigurationError as exc:
            raise exceptions.EvaluationError(str(exc)) from exc
        return pd_mod, active, project_id or "", location

    def _run_registry_eval(
        self,
        df: Any,
        metrics: Sequence[Any],
        project_id: str,
        default_loc: str,
        pd_mod: Any,
    ) -> _DetailedEvalOutput:
        eval_loc = _vertex_metrics.infer_genai_location(
            metrics, self._config, default_loc
        )
        client = self._client or vertexai.Client(
            project=project_id, location=eval_loc
        )
        has_ctx = "context" in df.columns
        resolved_metrics = [
            _vertex_metrics.resolve_genai_metric(m, has_context=has_ctx)
            for m in metrics
        ]
        response = client.evals.evaluate(dataset=df, metrics=resolved_metrics)
        table, explanations = _vertex_metrics.extract_genai_case_results(
            df, response
        )
        scores, raw_scores = _vertex_metrics.collect_genai_summary(
            response, metrics, table, pd_mod, self._config.normalize_scores
        )
        return scores, raw_scores, explanations, table, project_id

    def _invoke_eval_task(
        self,
        df: Any,
        resolved_metrics: Sequence[Any],
        eval_mod: Any,
        run_name: str | None,
    ) -> Any:
        cfg = self._config
        task_cls = self._eval_task_factory or eval_mod.EvalTask
        task = task_cls(
            dataset=df,
            metrics=resolved_metrics,
            experiment=cfg.experiment,
            metric_column_mapping=cfg.metric_column_mapping or None,
            output_uri_prefix=cfg.output_uri_prefix or "",
        )
        run_id = (
            (run_name or cfg.experiment_run_name) if cfg.experiment else None
        )
        return task.evaluate(
            model=cfg.model,
            prompt_template=cfg.prompt_template,
            experiment_run_name=run_id,
            retry_timeout=float(cfg.retry_timeout),
            evaluation_service_qps=cfg.evaluation_service_qps,
        )

    def _run_eval_task(
        self,
        df: Any,
        metrics: Sequence[Any],
        pd_mod: Any,
        project_id: str,
        location: str,
        run_name: str | None,
    ) -> _DetailedEvalOutput:
        aiplatform.init(project=project_id, location=location)
        vertexai.init(project=project_id, location=location)
        eval_mod = _get_lazy_module("evaluation")
        pm_mod = _get_lazy_module("pm")
        resolved = [
            _vertex_metrics.resolve_eval_task_metric(
                m,
                has_context="context" in df.columns,
                eval_mod=eval_mod,
                pm_mod=pm_mod,
            )
            for m in metrics
        ]
        run = self._invoke_eval_task(df, resolved, eval_mod, run_name)
        scores, raw, expls, table = _vertex_metrics.collect_task_stats(
            metrics, run, pd_mod, self._config.normalize_scores
        )
        return scores, raw, expls, table, project_id

    def _run_detailed(
        self,
        dataset: Sequence[Mapping[str, Any]],
        metrics: Sequence[Any] | None = None,
        run_name: str | None = None,
    ) -> _DetailedEvalOutput:
        pd_mod, active, project_id, location = self._prepare_context(
            dataset, metrics
        )
        df = pd_mod.DataFrame([dict(row) for row in dataset])
        try:
            if any(_vertex_metrics.is_metric_uri(m) for m in active):
                return self._run_registry_eval(
                    df, active, project_id, location, pd_mod
                )
            return self._run_eval_task(
                df, active, pd_mod, project_id, location, run_name
            )
        except exceptions.EvaluationError:
            raise
        except Exception as exc:
            raise exceptions.EvaluationError(
                f"Vertex eval failed: {exc}"
            ) from exc

    @override
    def evaluate(
        self,
        dataset: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        return self._run_detailed(dataset, metrics)[0]

    @override
    def evaluate_trajectory(
        self,
        trajectory: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        active = list(
            metrics if metrics is not None else _TRAJECTORY_DEFAULT_METRICS
        )
        return self._run_detailed(
            [_trajectory_to_vertex_row(trajectory)], active
        )[0]

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
        metrics: Sequence[Any] | None = None,
        experiment_run_name: str | None = None,
        thresholds: Mapping[str, float] | None = None,
    ) -> models.EvaluationSuiteResult:
        """Evaluates dataset and returns per-instance EvaluationSuiteResult."""
        cfg = self._config
        ts = datetime.datetime.now(datetime.UTC).strftime("run-%Y%m%d-%H%M%S")
        run_id = experiment_run_name or cfg.experiment_run_name or ts
        scores, raw_scores, expls, table, proj = self._run_detailed(
            dataset, metrics, run_id
        )
        loc = cfg.location or settings.get_gcp_settings().location
        exp = cfg.experiment or "default"
        url = (
            f"{_CONSOLE_EXPERIMENTS_URL}/{loc}/experiments/{exp}"
            f"/runs/{run_id}?project={proj}"
        )
        return reporting.build_suite_result(
            dataset,
            scores,
            raw_scores,
            table=table,
            expls=expls,
            normalize=cfg.normalize_scores,
            experiment=exp,
            run_name=run_id,
            console_url=url,
            thresholds=thresholds,
        )
