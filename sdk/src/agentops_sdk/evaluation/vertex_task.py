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
from agentops_sdk.evaluation import _task_runner
from agentops_sdk.evaluation import _vertex_metrics
from agentops_sdk.evaluation import models
from agentops_sdk.evaluation import reporting
from agentops_sdk.ports import evaluation as eval_ports
from agentops_sdk.telemetry import provider

_DetailedEvalOutput: TypeAlias = tuple[
    dict[str, float], dict[str, float], dict[str, list[str]], Any, str
]
_TRAJECTORY_DEFAULT_METRICS = ("multi_turn_chat_quality", "multi_turn_safety")
_CONSOLE_EXPERIMENTS_URL = (
    "https://console.cloud.google.com/vertex-ai/experiments/locations"
)
_LAZY_SUBMODULES = {
    "evaluation": "vertexai.evaluation",
    "evaluation_service": (
        "google.cloud.aiplatform_v1beta1.types.evaluation_service"
    ),
    "pm": "vertexai.preview.evaluation.metrics",
    "preview_evaluation": "vertexai.preview.evaluation",
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


def _resolve_task_target(
    cfg: models.VertexAIEvalConfig,
    proj: str,
    loc: str,
    factory: Callable[..., Any] | None,
    metrics: Sequence[Any],
) -> tuple[Any, Any | None, bool]:
    ar_cfg = _task_runner.build_autorater_config(
        cfg, proj, loc, _get_lazy_module
    )
    if factory is not None:
        return factory, ar_cfg, False
    eval_mod = _get_lazy_module("evaluation")
    needs_preview = ar_cfg is not None or any(
        isinstance(m, str)
        and m.strip().lower().startswith("trajectory_single_tool_use:")
        for m in metrics
    )
    eval_cls = getattr(eval_mod, "EvalTask", None)
    is_real_eval = getattr(
        eval_mod, "__name__", ""
    ) == "vertexai.evaluation" and getattr(
        eval_cls, "__module__", ""
    ).startswith("vertexai.")
    if needs_preview and is_real_eval:
        preview_mod = _get_lazy_module("preview_evaluation")
        preview_cls = preview_mod.EvalTask
        is_real_preview = getattr(preview_cls, "__module__", "").startswith(
            "vertexai."
        )
        return preview_cls, ar_cfg, is_real_preview
    return eval_cls, ar_cfg, False


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
        target = _resolve_task_target(
            self._config, project_id, location, self._eval_task_factory, metrics
        )
        run = _vertex_metrics.run_eval_task_instance(
            self._config, df, resolved, target, run_name
        )
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
            provider.record_fallback_metric("evaluation", "vertex_eval_error")
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
            [_vertex_metrics.trajectory_to_vertex_row(trajectory)], active
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
        active = list(metrics if metrics is not None else cfg.metrics)
        exp_clean = (cfg.experiment or "").strip()
        has_uri = any(_vertex_metrics.is_metric_uri(m) for m in active)
        url = (
            f"{_CONSOLE_EXPERIMENTS_URL}/{loc}/experiments/{exp_clean}"
            f"/runs/{run_id}?project={proj}"
            if exp_clean and not has_uri
            else None
        )
        return reporting.build_suite_result(
            dataset,
            scores,
            raw_scores,
            table=table,
            expls=expls,
            normalize=cfg.normalize_scores,
            experiment=exp_clean or "default",
            run_name=run_id,
            console_url=url,
            thresholds=thresholds,
        )
