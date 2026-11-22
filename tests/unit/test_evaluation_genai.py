# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for VertexEvalTaskAdapter, ExperimentEngine, and GenAI evals."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from agentops_sdk.evaluation import CustomRubricMetric
from agentops_sdk.evaluation import EvaluationResult
from agentops_sdk.evaluation import ExperimentConfig
from agentops_sdk.evaluation import ExperimentEngine
from agentops_sdk.evaluation import LocalExperimentTracker
from agentops_sdk.evaluation import UserScenario
from agentops_sdk.evaluation import UserScenarioGenerationConfig
from agentops_sdk.evaluation import UserSimulatorConfig
from agentops_sdk.evaluation import VertexAIEvalConfig
from agentops_sdk.evaluation import VertexEvalTaskAdapter
from agentops_sdk.evaluation import delete_registered_metric
from agentops_sdk.evaluation import evaluate_agent_engine
from agentops_sdk.evaluation import genai_evals
from agentops_sdk.evaluation import generate_conversation_scenarios
from agentops_sdk.evaluation import generate_loss_clusters
from agentops_sdk.evaluation import get_registered_metric
from agentops_sdk.evaluation import list_registered_metrics
from agentops_sdk.evaluation import normalize_agent_info
from agentops_sdk.evaluation import register_code_execution_metric
from agentops_sdk.evaluation import register_llm_metric
from agentops_sdk.evaluation import simulate_agent_conversations


def _make_task_cls() -> type[Any]:
    class _Task:
        def __init__(self, **_kw: Any) -> None:
            pass

        def evaluate(self, **_kw: Any) -> SimpleNamespace:
            return SimpleNamespace(
                summary_metrics={
                    "accuracy/mean": 4.5,
                    "trajectory_single_tool_use/mean": 1.0,
                },
                metrics_table=pd.DataFrame(
                    {
                        "accuracy/score": [4.5],
                        "accuracy/explanation": ["grounded"],
                        "trajectory_single_tool_use/score": [1.0],
                    }
                ),
            )

    return _Task


def test_vertex_eval_task_suite_and_rubric() -> None:
    rubric = CustomRubricMetric(
        name="accuracy", criteria="Be accurate", rubric={1: "no", 5: "yes"}
    )
    cfg = VertexAIEvalConfig(
        project_id="test-proj",
        location="europe-west4",
        experiment="exp-1",
        metrics=[rubric, "trajectory_single_tool_use:lookup_tool"],
    )
    suite = VertexEvalTaskAdapter(
        cfg, eval_task_factory=_make_task_cls()
    ).evaluate_suite(
        [{"prompt": "p", "context": "c", "response": "r"}],
        thresholds={"accuracy": 0.8},
    )
    assert suite.all_passed is True
    assert "console.cloud.google.com" in (suite.console_url or "")


def test_vertex_eval_task_mixed_uri_and_case_results() -> None:
    uri = "projects/p/locations/europe-west4/evaluationMetrics/custom_1"
    case_res = SimpleNamespace(
        eval_case_index=0,
        response_candidate_results=[
            SimpleNamespace(
                metric_results={
                    "groundedness": SimpleNamespace(
                        score=1.0, explanation="Supported by context"
                    )
                }
            )
        ],
    )
    fake_client = SimpleNamespace(
        evals=SimpleNamespace(
            evaluate=lambda **_kw: SimpleNamespace(
                summary_metrics=[
                    SimpleNamespace(metric_name="custom_1", mean_score=4.0)
                ],
                eval_case_results=[case_res],
            )
        )
    )
    cfg = VertexAIEvalConfig(project_id="test-proj", location="europe-west4")
    suite = VertexEvalTaskAdapter(cfg, client=fake_client).evaluate_suite(
        [{"prompt": "p", "context": "ctx", "response": "ctx"}],
        metrics=[uri, "groundedness"],
        thresholds={"custom_1": 0.7, "groundedness": 0.9},
    )
    assert suite.summary_scores == {"custom_1": 0.8, "groundedness": 1.0}
    assert suite.instances[0].metrics["groundedness"].explanation == (
        "Supported by context"
    )


def test_experiment_engine_local_tracker_and_dto_fields(
    tmp_path: Path,
) -> None:
    assert "raw_response" not in EvaluationResult.model_fields
    out_file = tmp_path / "results.json"
    cfg = ExperimentConfig(
        name="exp-local",
        prompt_template="{prompt}",
        variants={"a": {"k": 1}, "b": {"k": 2}},
        dataset=[{"prompt": "p", "context": "c", "response": "c"}],
        evaluator="in_memory",
        tracking_backend="local",
        thresholds={"groundedness": 0.5},
    )
    engine = ExperimentEngine(cfg)
    assert isinstance(engine.tracker, LocalExperimentTracker)
    res = engine.run(output_path=str(out_file))
    assert out_file.exists() and len(engine.tracker.runs) == 2
    assert engine.get_winner(res, "groundedness") == "a"
    assert len(engine.get_summary_table(res)) == 2
    with pytest.raises(ValueError):
        engine.get_winner(res, "missing_metric")
    with pytest.raises(ValueError):
        ExperimentEngine(cfg.model_copy(update={"variants": {}})).run()


def _evals_payloads() -> tuple[
    SimpleNamespace, SimpleNamespace, SimpleNamespace
]:
    cluster = SimpleNamespace(
        cluster_id="c1",
        item_count=2,
        taxonomy_entry=SimpleNamespace(
            l1_category="hallucination",
            l2_category="context",
            description="Unsupported claim",
        ),
        examples=[],
    )
    summary = SimpleNamespace(
        metrics={"multi_turn_task_success_v1": 0.92},
        total_items=2,
        failed_items=0,
    )
    case = SimpleNamespace(
        eval_case_id="sc-1",
        user_scenario=SimpleNamespace(
            starting_prompt="Hi",
            conversation_plan="Ask about oil",
            test_case_title="Oil",
        ),
    )
    return cluster, summary, case


def _make_fake_evals(reg_uri: str) -> tuple[SimpleNamespace, list[Any]]:
    run_calls: list[Any] = []
    cluster, summary, case = _evals_payloads()
    run_name = "projects/test-proj/locations/europe-west4/evaluationRuns/r1"
    evals = SimpleNamespace(
        generate_loss_clusters=lambda **_kw: SimpleNamespace(
            results=[
                SimpleNamespace(analysis_time="2026-01-01", clusters=[cluster])
            ]
        ),
        create_evaluation_metric=lambda **_kw: reg_uri,
        list_evaluation_metrics=lambda **_kw: SimpleNamespace(
            evaluation_metrics=[
                SimpleNamespace(name=reg_uri, display_name="custom_m")
            ]
        ),
        get_evaluation_metric=lambda **_kw: SimpleNamespace(
            name=reg_uri, display_name="custom_m"
        ),
        delete_evaluation_metric=lambda **_kw: None,
        create_evaluation_run=lambda **kw: (
            run_calls.append(kw) or SimpleNamespace(name=run_name)
        ),
        get_evaluation_run=lambda **_kw: SimpleNamespace(
            name=run_name,
            state="SUCCEEDED",
            evaluation_run_results=SimpleNamespace(
                summary_metrics=summary, loss_analysis_results=None
            ),
        ),
        generate_conversation_scenarios=lambda **_kw: SimpleNamespace(
            eval_cases=[case]
        ),
    )
    return evals, run_calls


def test_genai_evals_loss_and_metric_registry() -> None:
    genai_evals.clear_metric_registry_cache()
    reg_uri = "projects/test-proj/locations/global/evaluationMetrics/m1"
    fake_evals, _ = _make_fake_evals(reg_uri)
    client = SimpleNamespace(evals=fake_evals)
    loss = generate_loss_clusters(
        {}, metric="groundedness", project_id="test-proj", client=client
    )
    assert loss.total_failures == 2 and loss.clusters[0].percentage == 100.0
    m1 = register_code_execution_metric(
        "custom_m",
        "def evaluate(x): return 1.0",
        project_id="test-proj",
        client=client,
    )
    assert m1.resource_name == reg_uri
    assert register_llm_metric(
        "llm_m", "Judge {x}", project_id="test-proj", client=client
    )
    assert (
        len(list_registered_metrics(project_id="test-proj", client=client)) == 1
    )
    assert get_registered_metric(
        "custom_m", project_id="test-proj", client=client
    )
    assert delete_registered_metric(
        "custom_m", project_id="test-proj", client=client
    )


def test_genai_evals_agent_engine_defaults_and_scenarios() -> None:
    fake_evals, run_calls = _make_fake_evals(
        "projects/test-proj/locations/global/evaluationMetrics/m1"
    )
    client = SimpleNamespace(evals=fake_evals)
    run = evaluate_agent_engine(
        "engine-1",
        [{"prompt": "hi"}],
        dest_gcs_uri="gs://eval-bucket/out",
        project_id="test-proj",
        location="europe-west4",
        client=client,
    )
    assert run.state == "SUCCEEDED" and run.total_items == 2
    assert run_calls[0]["agent_info"].root_agent_id == "root"
    assert run_calls[0]["agent_info"].name == "agent_under_test"
    assert [m.metric for m in run_calls[0]["metrics"]] == [
        "multi_turn_tool_use_quality_v1",
        "multi_turn_task_success_v1",
    ]
    with pytest.raises(ValueError):
        evaluate_agent_engine("engine-1", [{}], dest_gcs_uri="http://bad")
    info = normalize_agent_info(
        {"name": "advisor", "agents": [{"agent_id": "a1"}]}
    )
    assert info.root_agent_id == "root"
    scs = generate_conversation_scenarios(
        info,
        UserScenarioGenerationConfig(count=1),
        project_id="test-proj",
        client=client,
    )
    assert scs[0].goal == "Ask about oil"


def test_simulate_agent_conversations_stops_on_done() -> None:
    fake_sim = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda **_kw: SimpleNamespace(text="[DONE]")
        )
    )
    scenarios = [
        UserScenario(
            scenario_id="s1", starting_prompt="Need filter", goal="Find filter"
        )
    ]
    trajs = simulate_agent_conversations(
        scenarios,
        lambda msg: {"content": f"Echo: {msg}", "tool_calls": []},
        UserSimulatorConfig(max_turns=3),
        project_id="test-proj",
        location="europe-west4",
        client=fake_sim,
    )
    assert trajs[0].completed is True and trajs[0].turns_count == 2
    with pytest.raises(TypeError):
        simulate_agent_conversations(scenarios, "not-callable")


def test_gemini_38_flash_defaults_and_env_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENTOPS_EVAL_AUTORATER_MODEL", raising=False)
    monkeypatch.delenv("AGENTOPS_EVAL_JUDGE_MODEL", raising=False)
    assert VertexAIEvalConfig().judge_model == "gemini-3.8-flash"
    assert VertexAIEvalConfig().judge_location == "global"
    assert UserSimulatorConfig().model_name == "gemini-3.8-flash"

    monkeypatch.setenv("AGENTOPS_EVAL_JUDGE_MODEL", "gemini-judge-fallback")
    assert VertexAIEvalConfig().judge_model == "gemini-judge-fallback"

    monkeypatch.setenv("AGENTOPS_EVAL_AUTORATER_MODEL", "gemini-autorater-top")
    assert VertexAIEvalConfig().judge_model == "gemini-autorater-top"


def test_preview_eval_task_autorater_and_trajectory_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentops_sdk.evaluation import vertex_task

    monkeypatch.setattr(vertex_task.aiplatform, "init", lambda **_kw: None)
    monkeypatch.setattr(vertex_task.vertexai, "init", lambda **_kw: None)
    captured: list[dict[str, Any]] = []

    class _FakePreviewEvalTask:
        __module__ = "vertexai.preview.evaluation.eval_task"

        def __init__(self, **kw: Any) -> None:
            captured.append(kw)

        def evaluate(self, **_kw: Any) -> SimpleNamespace:
            return SimpleNamespace(
                summary_metrics={"groundedness/mean": 1.0},
                metrics_table=pd.DataFrame(
                    {
                        "groundedness/score": [1.0],
                        "groundedness/explanation": ["ok"],
                    }
                ),
            )

    preview_mod = vertex_task._get_lazy_module("preview_evaluation")
    monkeypatch.setattr(preview_mod, "EvalTask", _FakePreviewEvalTask)

    cfg = VertexAIEvalConfig(
        project_id="test-proj",
        location="europe-west4",
        judge_location="global",
        judge_model="gemini-3.8-flash",
    )
    scores = VertexEvalTaskAdapter(cfg).evaluate(
        [{"prompt": "p", "context": "c", "response": "c"}],
        metrics=["groundedness"],
    )
    assert scores == {"groundedness": 1.0}
    ar_cfg = captured[0]["autorater_config"]
    assert ar_cfg.autorater_model == (
        "projects/test-proj/locations/global/"
        "publishers/google/models/gemini-3.8-flash"
    )

    class _FakePreviewTrajTask(_FakePreviewEvalTask):
        def evaluate(self, **_kw: Any) -> SimpleNamespace:
            return SimpleNamespace(
                summary_metrics={"trajectory_single_tool_use/mean": 1.0},
                metrics_table=pd.DataFrame(
                    {"trajectory_single_tool_use/score": [1.0]}
                ),
            )

    monkeypatch.setattr(preview_mod, "EvalTask", _FakePreviewTrajTask)
    traj_cfg = VertexAIEvalConfig(
        project_id="test-proj",
        location="europe-west4",
        judge_model="",
    )
    traj_scores = VertexEvalTaskAdapter(traj_cfg).evaluate(
        [
            {
                "prompt": "p",
                "response": "r",
                "predicted_trajectory": [{"tool_name": "lookup_tool"}],
            }
        ],
        metrics=["trajectory_single_tool_use:lookup_tool"],
    )
    assert traj_scores == {"trajectory_single_tool_use": 1.0}
    assert "autorater_config" not in captured[1]


def test_vertex_eval_backend_failure_records_fallback_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentops_sdk.domain import exceptions
    from agentops_sdk.evaluation import vertex_task
    from agentops_sdk.telemetry import provider

    monkeypatch.setattr(vertex_task.aiplatform, "init", lambda **_kw: None)
    monkeypatch.setattr(vertex_task.vertexai, "init", lambda **_kw: None)
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(
        provider,
        "record_fallback_metric",
        lambda component, reason: recorded.append((component, reason)),
    )

    class _FailingTask:
        def __init__(self, **_kw: Any) -> None:
            pass

        def evaluate(self, **_kw: Any) -> Any:
            raise RuntimeError("backend unavailable")

    cfg = VertexAIEvalConfig(project_id="test-proj", location="europe-west4")
    with pytest.raises(exceptions.EvaluationError, match="Vertex eval failed"):
        VertexEvalTaskAdapter(cfg, eval_task_factory=_FailingTask).evaluate(
            [{"prompt": "p", "context": "c", "response": "r"}],
            metrics=["groundedness"],
        )
    assert recorded == [("evaluation", "vertex_eval_error")]
