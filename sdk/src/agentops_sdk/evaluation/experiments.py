# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Prompt experiment engine with bounded sandboxed templating and trackers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import os
import pathlib
import threading
from typing import Any, Literal

from google import genai
from google.cloud import aiplatform
import pydantic
from typing_extensions import override
import vertexai

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import predicates
from agentops_sdk.domain import templating
from agentops_sdk.evaluation import in_memory
from agentops_sdk.evaluation import models
from agentops_sdk.evaluation import simulator
from agentops_sdk.ports import evaluation as eval_ports
from agentops_sdk.telemetry import _sink_helpers

_DEFAULT_EXPERIMENT_METRICS = ("groundedness", "question_answering_relevance")

# Re-exported for backwards compatibility with existing callers/tests.
normalize_agent_info = simulator.normalize_agent_info
generate_conversation_scenarios = simulator.generate_conversation_scenarios
simulate_agent_conversations = simulator.simulate_agent_conversations
_KEEP_GENAI_REF = (genai, vertexai)


class ExperimentConfig(pydantic.BaseModel):
    """Configuration for multi-variant prompt evaluation experiments."""

    name: str
    description: str = ""
    prompt_template: str
    variants: dict[str, dict[str, Any]]
    dataset: list[dict[str, Any]]
    metrics: list[str] = pydantic.Field(
        default_factory=lambda: list(_DEFAULT_EXPERIMENT_METRICS)
    )
    evaluator: str | eval_ports.EvaluatorPort | Sequence[Any] = "in_memory"
    evaluator_kwargs: dict[str, Any] = pydantic.Field(default_factory=dict)
    tracking_backend: Literal["none", "local", "vertex_ai"] = "none"
    project_id: str | None = None
    location: str | None = None
    thresholds: dict[str, float] = pydantic.Field(default_factory=dict)
    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True)


class LocalExperimentTracker(eval_ports.ExperimentTrackerPort):
    """Thread-safe in-memory ExperimentTrackerPort without cloud coupling."""

    def __init__(self) -> None:
        """Initializes an empty thread-safe local experiment tracker."""
        self._lock = threading.Lock()
        self._runs: list[dict[str, Any]] = []

    @override
    def log_run(
        self,
        experiment_name: str,
        run_name: str,
        params: Mapping[str, Any],
        metrics: Mapping[str, float | int | str],
    ) -> None:
        entry = {
            "experiment_name": experiment_name,
            "run_name": run_name,
            "params": {str(k): str(v) for k, v in params.items()},
            "metrics": dict(metrics),
        }
        with self._lock:
            self._runs.append(entry)

    @property
    def runs(self) -> list[dict[str, Any]]:
        """Returns a thread-safe snapshot of logged experiment runs."""
        with self._lock:
            return list(self._runs)

    def clear(self) -> None:
        """Clears all locally recorded experiment runs."""
        with self._lock:
            self._runs.clear()


class _VertexExperimentTracker(eval_ports.ExperimentTrackerPort):
    """ExperimentTrackerPort adapter backed by Vertex AI Experiments."""

    def __init__(
        self, project_id: str | None = None, location: str | None = None
    ) -> None:
        """Initializes the Vertex AI experiment tracker."""
        self._project_id = project_id
        self._location = location

    @override
    def log_run(
        self,
        experiment_name: str,
        run_name: str,
        params: Mapping[str, Any],
        metrics: Mapping[str, float | int | str],
    ) -> None:
        proj, loc = settings.resolve_gcp_project_and_location(
            self._project_id, self._location, require_project=True
        )
        aiplatform.init(project=proj, location=loc, experiment=experiment_name)
        aiplatform.start_run(run=run_name)
        try:
            aiplatform.log_params({str(k): str(v) for k, v in params.items()})
            aiplatform.log_metrics(dict(metrics))
        finally:
            aiplatform.end_run()


def _default_tracker(
    config: ExperimentConfig,
) -> eval_ports.ExperimentTrackerPort | None:
    if config.tracking_backend == "local":
        return LocalExperimentTracker()
    if config.tracking_backend == "vertex_ai":
        return _VertexExperimentTracker(
            project_id=config.project_id, location=config.location
        )
    return None


class ExperimentEngine:
    """Executes prompt experiment variants with sandboxed templating."""

    def __init__(
        self,
        config: ExperimentConfig,
        *,
        tracker: eval_ports.ExperimentTrackerPort | None = None,
    ) -> None:
        """Initializes the experiment engine with config and tracker."""
        self.config = config
        self._evaluator = in_memory.get_evaluator(
            config.evaluator, **config.evaluator_kwargs
        )
        self.tracker: eval_ports.ExperimentTrackerPort | None = (
            tracker if tracker is not None else _default_tracker(config)
        )

    def _run_variant(
        self, variant_name: str, variant_vars: Mapping[str, Any]
    ) -> models.EvaluationResult:
        cfg = self.config
        rows = [
            {
                **row,
                "prompt": templating.render_sandboxed_template(
                    cfg.prompt_template, {**variant_vars, **row}
                ),
            }
            for row in cfg.dataset
        ]
        scores = self._evaluator.evaluate(rows, metrics=cfg.metrics)
        failed = {
            name: (scores.get(name, 0.0), threshold)
            for name, threshold in cfg.thresholds.items()
            if name not in scores
            or not predicates.MetricThresholdPredicate(
                name, threshold, "ge"
            ).evaluate(scores)
        }
        run_name = f"{cfg.name}-{variant_name}"
        result = models.EvaluationResult(
            scores=scores,
            raw_scores=scores,
            is_passing=not failed,
            failed_metrics=failed,
            run_name=run_name,
            eval_mode=getattr(self._evaluator, "provider_name", "custom"),
        )
        if self.tracker is not None:
            metrics_payload: dict[str, float | int | str] = dict(result.scores)
            self.tracker.log_run(
                cfg.name, run_name, variant_vars, metrics_payload
            )
        return result

    def run(
        self, output_path: str | None = None
    ) -> dict[str, models.EvaluationResult]:
        """Executes variants and optionally writes validated JSON results."""
        if not self.config.variants:
            raise ValueError("variants must not be empty.")
        results = {
            name: self._run_variant(name, variant_vars)
            for name, variant_vars in self.config.variants.items()
        }
        if output_path is not None:
            safe_path = _sink_helpers.validate_local_sink_path(output_path)
            parent = os.path.dirname(safe_path)
            if parent:
                os.makedirs(parent, mode=0o700, exist_ok=True)
            serialized = json.dumps(
                {k: res.model_dump() for k, res in results.items()}
            )
            pathlib.Path(safe_path).write_text(serialized, encoding="utf-8")
        return results

    def get_winner(
        self, results: Mapping[str, models.EvaluationResult], metric: str
    ) -> str:
        """Returns variant name with highest score for metric."""
        candidates = {
            name: res.scores[metric]
            for name, res in results.items()
            if metric in res.scores
        }
        if not candidates:
            raise ValueError(f"Metric {metric!r} missing from results.")
        return max(candidates, key=lambda name: candidates[name])

    def get_summary_table(
        self, results: Mapping[str, models.EvaluationResult]
    ) -> Any:
        """Returns a pandas DataFrame comparing variant scores."""
        try:
            import pandas as pd_mod
        except ImportError as exc:
            raise exceptions.EvaluationError(
                "pandas is required for summary tables; "
                "install agentops-sdk[eval]."
            ) from exc
        rows = [
            {"variant": name, "is_passing": res.is_passing, **res.scores}
            for name, res in results.items()
        ]
        return pd_mod.DataFrame(rows)
