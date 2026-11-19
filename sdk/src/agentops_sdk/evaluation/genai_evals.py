# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Vertex AI GenAI Evals loss analysis and Agent Engine evaluation runs."""

from __future__ import annotations

from collections.abc import Sequence
import re
import time
from typing import Any

from google.genai import types as genai_types
import vertexai
from vertexai._genai import types as vtypes
from vertexai._genai.types import evals as eval_types

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.evaluation import cloud_models
from agentops_sdk.evaluation import metric_registry
from agentops_sdk.evaluation import simulator

_TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
_GCS_URI_RE = re.compile(r"^gs://[a-z0-9][a-z0-9._-]{1,220}(/.*)?$")
_DEFAULT_AGENT_ENGINE_METRICS = (
    "multi_turn_tool_use_quality_v1",
    "multi_turn_task_success_v1",
)
_VERSIONED_METRIC_ALIASES = {
    "multi_turn_tool_use_quality": "multi_turn_tool_use_quality_v1",
    "multi_turn_task_success": "multi_turn_task_success_v1",
}

# Re-export registry, cache, and simulator symbols for backwards compatibility.
_CACHE = metric_registry._CACHE
clear_metric_registry_cache = metric_registry.clear_metric_registry_cache
register_code_execution_metric = metric_registry.register_code_execution_metric
register_llm_metric = metric_registry.register_llm_metric
list_registered_metrics = metric_registry.list_registered_metrics
get_registered_metric = metric_registry.get_registered_metric
delete_registered_metric = metric_registry.delete_registered_metric
generate_conversation_scenarios = simulator.generate_conversation_scenarios
simulate_agent_conversations = simulator.simulate_agent_conversations
normalize_agent_info = simulator.normalize_agent_info


def generate_loss_clusters(
    eval_result: Any,
    *,
    metric: Any | None = None,
    candidate: str | None = None,
    max_clusters: int = 5,
    project_id: str | None = None,
    client: Any | None = None,
) -> cloud_models.AutoLossAnalysisResult:
    """Runs global Vertex AI auto-loss clustering on an EvaluationResult."""
    proj, _ = settings.resolve_gcp_project_and_location(
        project_id, "global", require_project=True
    )
    loss_cfg = vtypes.LossAnalysisConfig(max_top_cluster_count=max_clusters)
    try:
        active = client or vertexai.Client(project=proj, location="global")
        response = active.evals.generate_loss_clusters(
            eval_result=eval_result,
            metric=metric,
            candidate=candidate,
            config=loss_cfg,
        )
    except exceptions.EvaluationError:
        raise
    except Exception as exc:
        raise exceptions.EvaluationError(
            f"generate_loss_clusters failed: {exc}"
        ) from exc
    return cloud_models.AutoLossAnalysisResult.from_cloud_response(
        response, str(metric or "unknown")
    )


def _wrap_agent_engine_metric(metric: Any) -> Any:
    if isinstance(metric, vtypes.EvaluationRunMetric):
        return metric
    if isinstance(metric, str) and metric.startswith("projects/"):
        short_name = metric.rsplit("/", 1)[-1]
        return vtypes.EvaluationRunMetric(
            metric=short_name, metric_resource_name=metric
        )
    raw_name = str(metric).strip()
    resolved_name = _VERSIONED_METRIC_ALIASES.get(raw_name.lower(), raw_name)
    spec = genai_types.PredefinedMetricSpec(metric_spec_name=resolved_name)
    unified = vtypes.UnifiedMetric(predefined_metric_spec=spec)
    return vtypes.EvaluationRunMetric(
        metric=resolved_name, metric_config=unified
    )


def _build_agent_engine_dataset(dataset_input: Any) -> Any:
    if isinstance(
        dataset_input,
        (vtypes.EvaluationDataset, vtypes.EvaluationRunDataSource),
    ):
        return dataset_input
    try:
        import pandas as pd_mod
    except ImportError as exc:
        raise exceptions.EvaluationError(
            "pandas is required; install agentops-sdk[eval]."
        ) from exc
    df = (
        dataset_input
        if isinstance(dataset_input, pd_mod.DataFrame)
        else pd_mod.DataFrame(dataset_input)
    )
    return vtypes.EvaluationDataset(eval_dataset_df=df)


def _build_simulator_config(
    sim_cfg: cloud_models.UserSimulatorConfig | None,
) -> Any:
    if sim_cfg is None:
        return None
    return eval_types.UserSimulatorConfig(
        max_turn=sim_cfg.max_turns, model_name=sim_cfg.model_name
    )


def _poll_evaluation_run(
    client: Any, run_name: str, timeout_seconds: float, poll_interval: float
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    while True:
        run = client.evals.get_evaluation_run(name=run_name)
        raw_state = getattr(run, "state", "")
        state = str(getattr(raw_state, "value", raw_state))
        if state in _TERMINAL_STATES:
            if state != "SUCCEEDED":
                raise exceptions.EvaluationError(f"Run {run_name!r}: {state}")
            return run
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            raise exceptions.EvaluationError(f"Run {run_name!r} timed out.")
        time.sleep(min(poll_interval, max(0.01, remaining)))


def _submit_and_await_run(
    proj: str | None,
    loc: str,
    run_kwargs: dict[str, Any],
    *,
    wait_for_completion: bool,
    timeout_seconds: float,
    poll_interval_seconds: float,
    client: Any | None,
) -> Any:
    try:
        active = client or vertexai.Client(project=proj, location=loc)
        run = active.evals.create_evaluation_run(**run_kwargs)
        if not wait_for_completion:
            return run
        return _poll_evaluation_run(
            active, str(run.name), timeout_seconds, poll_interval_seconds
        )
    except exceptions.EvaluationError:
        raise
    except Exception as exc:
        raise exceptions.EvaluationError(
            f"Agent Engine eval failed: {exc}"
        ) from exc


def _prepare_run_context(
    agent_engine_id: str,
    dataset: Any,
    dest_gcs_uri: str,
    metrics: Sequence[Any] | None,
    user_simulator_config: cloud_models.UserSimulatorConfig | None,
    agent_info: Any | None,
    evaluation_experiment: str | None,
    project_id: str | None,
    location: str | None,
) -> tuple[str | None, str, dict[str, Any]]:
    if not _GCS_URI_RE.fullmatch(dest_gcs_uri) or ".." in dest_gcs_uri:
        raise ValueError(f"Invalid dest_gcs_uri: {dest_gcs_uri!r}")
    proj, loc = settings.resolve_gcp_project_and_location(
        project_id, location, require_project=True
    )
    run_kwargs = {
        "agent": agent_engine_id,
        "dataset": _build_agent_engine_dataset(dataset),
        "dest": dest_gcs_uri,
        "metrics": [
            _wrap_agent_engine_metric(m)
            for m in (metrics or _DEFAULT_AGENT_ENGINE_METRICS)
        ],
        "evaluation_experiment": evaluation_experiment,
        "user_simulator_config": _build_simulator_config(user_simulator_config),
        "agent_info": simulator.normalize_agent_info(agent_info),
    }
    return proj, loc, run_kwargs


def evaluate_agent_engine(
    agent_engine_id: str,
    dataset: Any,
    *,
    dest_gcs_uri: str,
    metrics: Sequence[Any] | None = None,
    user_simulator_config: cloud_models.UserSimulatorConfig | None = None,
    agent_info: Any | None = None,
    evaluation_experiment: str | None = None,
    wait_for_completion: bool = True,
    timeout_seconds: float = 900.0,
    poll_interval_seconds: float = 5.0,
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> cloud_models.CloudEvaluationRunResult:
    """Submits and optionally polls a managed Agent Engine evaluation run."""
    proj, loc, run_kwargs = _prepare_run_context(
        agent_engine_id,
        dataset,
        dest_gcs_uri,
        metrics,
        user_simulator_config,
        agent_info,
        evaluation_experiment,
        project_id,
        location,
    )
    final_run = _submit_and_await_run(
        proj,
        loc,
        run_kwargs,
        wait_for_completion=wait_for_completion,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        client=client,
    )
    return cloud_models.CloudEvaluationRunResult.from_cloud_run(
        final_run, proj or "", loc, experiment=evaluation_experiment
    )
