# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""LSP contract tests for EvaluatorPort and ExperimentEngine."""

from __future__ import annotations

import math
import sys
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from agentops_sdk.domain import exceptions
from agentops_sdk.evaluation import CompositeEvaluator
from agentops_sdk.evaluation import ExperimentConfig
from agentops_sdk.evaluation import ExperimentEngine
from agentops_sdk.evaluation import HeuristicEvaluator
from agentops_sdk.evaluation import InMemoryEvaluator
from agentops_sdk.evaluation import RagasEvaluator
from agentops_sdk.evaluation import VertexAIEvalConfig
from agentops_sdk.evaluation import VertexEvalTaskAdapter
from agentops_sdk.evaluation import experiments as eval_experiments
from agentops_sdk.ports import evaluation as eval_ports


def _metric_name(metric: Any) -> str:
    if isinstance(metric, str):
        return metric.split(":", 1)[0]
    return str(getattr(metric, "metric_name", None) or metric.metric)


class _FakeEvalTask:
    def __init__(
        self, *, dataset: pd.DataFrame, metrics: list[Any], **_kw: Any
    ):
        self.dataset = dataset
        self.metrics = metrics

    def evaluate(self, **_kw: Any) -> SimpleNamespace:
        summary: dict[str, float] = {}
        cols: dict[str, list[float]] = {}
        for metric in self.metrics:
            name = _metric_name(metric)
            summary[f"{name}/mean"] = 0.9
            cols[f"{name}/score"] = [0.9] * len(self.dataset)
        return SimpleNamespace(
            summary_metrics=summary, metrics_table=pd.DataFrame(cols)
        )


def _fake_ragas_client(result_factory: Any | None = None) -> SimpleNamespace:
    metrics_ns = SimpleNamespace(
        faithfulness=SimpleNamespace(name="faithfulness"),
        answer_relevancy=SimpleNamespace(name="answer_relevancy"),
    )

    def _evaluate(_ds: Any, *, metrics: list[Any]) -> Any:
        if result_factory is not None:
            return result_factory(metrics)
        return {getattr(m, "name", str(m)): 0.88 for m in metrics}

    return SimpleNamespace(
        evaluate=_evaluate,
        Dataset=SimpleNamespace(from_dict=lambda payload: payload),
        metrics=metrics_ns,
    )


@pytest.fixture(
    params=["in_memory", "heuristic", "composite", "ragas", "vertex_ai"]
)
def evaluator(request: pytest.FixtureRequest) -> eval_ports.EvaluatorPort:
    """Provides EvaluatorPort implementations via clean dependency injection."""
    kind = request.param
    if kind == "in_memory":
        return InMemoryEvaluator()
    if kind == "heuristic":
        return HeuristicEvaluator(metrics=("keyword_coverage", "similarity"))
    if kind == "composite":
        return CompositeEvaluator(
            [InMemoryEvaluator(), HeuristicEvaluator(metrics=("similarity",))]
        )
    if kind == "ragas":
        return RagasEvaluator(client=_fake_ragas_client())
    cfg = VertexAIEvalConfig(
        project_id="test-proj",
        location="europe-west4",
        metrics=["groundedness", "question_answering_relevance"],
    )
    return VertexEvalTaskAdapter(cfg, eval_task_factory=_FakeEvalTask)


def _sample_dataset() -> list[dict[str, Any]]:
    return [
        {
            "prompt": "What tractor oil viscosity is recommended?",
            "context": "Recommended tractor oil viscosity is 15W-40.",
            "response": "Recommended tractor oil viscosity is 15W-40.",
            "reference": "Recommended tractor oil viscosity is 15W-40.",
        }
    ]


def _sample_trajectory() -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "Check tractor oil viscosity"},
        {"role": "tool", "content": "Recommended tractor oil viscosity 15W-40"},
        {
            "role": "assistant",
            "content": "Recommended tractor oil viscosity is 15W-40.",
            "tool_calls": [{"name": "lookup_manual", "args": {"id": "1"}}],
        },
    ]


async def test_evaluator_port_contract(
    evaluator: eval_ports.EvaluatorPort,
) -> None:
    assert isinstance(evaluator, eval_ports.EvaluatorPort)
    scores = evaluator.evaluate(_sample_dataset())
    assert scores
    assert all(
        isinstance(v, float) and math.isfinite(v) for v in scores.values()
    )
    a_scores = await evaluator.aevaluate(_sample_dataset())
    assert a_scores == scores

    chosen = sorted(scores)[-1]
    filtered = evaluator.evaluate(_sample_dataset(), metrics=[chosen])
    assert set(filtered) == {chosen}

    traj_scores = evaluator.evaluate_trajectory(_sample_trajectory())
    assert traj_scores
    with pytest.raises(exceptions.EvaluationError):
        evaluator.evaluate([])
    with pytest.raises(exceptions.EvaluationError):
        await evaluator.aevaluate([])
    with pytest.raises(exceptions.EvaluationError):
        evaluator.evaluate_trajectory([])


def test_ragas_to_pandas_nonfinite_and_missing_dep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    df_obj = SimpleNamespace(
        to_pandas=lambda: pd.DataFrame(
            {"faithfulness": [0.8, 0.9], "answer_relevancy": [0.7, 0.9]}
        )
    )
    ev = RagasEvaluator(client=_fake_ragas_client(lambda _m: df_obj))
    res = ev.evaluate(_sample_dataset())
    assert res == {"faithfulness": pytest.approx(0.85), "answer_relevancy": 0.8}

    for bad_val in (float("nan"), float("inf"), float("-inf")):
        bad_df = SimpleNamespace(
            to_pandas=lambda v=bad_val: pd.DataFrame({"faithfulness": [v]})
        )
        bad_ev = RagasEvaluator(
            client=_fake_ragas_client(lambda _m, d=bad_df: d)
        )
        with pytest.raises(exceptions.EvaluationError, match="non-finite"):
            bad_ev.evaluate(_sample_dataset())

    monkeypatch.setitem(sys.modules, "ragas", None)
    monkeypatch.setitem(sys.modules, "datasets", None)
    with pytest.raises(exceptions.EvaluationError, match="not installed"):
        RagasEvaluator().evaluate(_sample_dataset())


def _patch_aiplatform(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    for name in ("init", "start_run", "log_params", "log_metrics"):
        monkeypatch.setattr(
            eval_experiments.aiplatform,
            name,
            lambda *args, _n=name, **kw: events.append((_n, args or kw)),
        )
    monkeypatch.setattr(
        eval_experiments.aiplatform,
        "end_run",
        lambda: events.append(("end_run", None)),
    )
    return events


def test_experiment_engine_contract_with_vertex_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _patch_aiplatform(monkeypatch)
    cfg = ExperimentConfig(
        name="oil-prompt-exp",
        prompt_template="Answer clearly: {prompt}",
        variants={"v1": {"tone": "concise"}, "v2": {"tone": "detailed"}},
        dataset=_sample_dataset(),
        evaluator="in_memory",
        tracking_backend="vertex_ai",
        project_id="test-proj",
        location="europe-west4",
        thresholds={"groundedness": 0.5},
    )
    results = ExperimentEngine(cfg).run()
    assert set(results) == {"v1", "v2"}
    assert results["v1"].is_passing is True
    assert [e[0] for e in events if e[0] == "start_run"] == [
        "start_run",
        "start_run",
    ]
    assert [e[0] for e in events if e[0] == "end_run"] == ["end_run", "end_run"]
