# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Internal EvalTask execution and trajectory formatting helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import inspect
import logging
from typing import Any

from agentops_sdk.evaluation import heuristics
from agentops_sdk.evaluation import models

_LOGGER = logging.getLogger(__name__)


def build_autorater_config(
    cfg: models.VertexAIEvalConfig,
    proj: str,
    loc: str,
    get_lazy_module: Callable[[str], Any],
) -> Any | None:
    """Builds AutoraterConfig from judge_model when configured."""
    judge = cfg.judge_model.strip()
    if not judge:
        return None
    if "/" in judge:
        ar_model = judge
    else:
        judge_loc = cfg.judge_location or loc
        ar_model = (
            f"projects/{proj}/locations/{judge_loc}"
            f"/publishers/google/models/{judge}"
        )
    eval_svc = get_lazy_module("evaluation_service")
    return eval_svc.AutoraterConfig(autorater_model=ar_model)


def accepts_autorater_config(factory: Any) -> bool:
    """Returns whether factory accepts an autorater_config keyword."""
    try:
        params = inspect.signature(factory).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        p.name == "autorater_config" or p.kind == inspect.Parameter.VAR_KEYWORD
        for p in params
    )


def run_eval_task_instance(
    cfg: models.VertexAIEvalConfig,
    df: Any,
    resolved: Sequence[Any],
    target: tuple[Any, Any | None, bool],
    run_name: str | None,
) -> Any:
    """Instantiates and runs EvalTask with optional AutoraterConfig."""
    task_cls, ar_cfg, force_ar = target
    kwargs: dict[str, Any] = {
        "dataset": df,
        "metrics": resolved,
        "experiment": cfg.experiment,
        "metric_column_mapping": cfg.metric_column_mapping or None,
        "output_uri_prefix": cfg.output_uri_prefix or "",
    }
    if ar_cfg is not None:
        if force_ar or accepts_autorater_config(task_cls):
            kwargs["autorater_config"] = ar_cfg
        else:
            _LOGGER.warning(
                "Configured judge_model=%r could not be applied because "
                "%r does not accept autorater_config.",
                cfg.judge_model,
                task_cls,
            )
    task = task_cls(**kwargs)
    run_id = (run_name or cfg.experiment_run_name) if cfg.experiment else None
    return task.evaluate(
        model=cfg.model,
        prompt_template=cfg.prompt_template,
        experiment_run_name=run_id,
        retry_timeout=float(cfg.retry_timeout),
        evaluation_service_qps=cfg.evaluation_service_qps,
    )


def trajectory_to_vertex_row(
    trajectory: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Converts a multi-turn trajectory into a Vertex EvalTask row."""
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
