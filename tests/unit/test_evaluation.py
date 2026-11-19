# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for offline evaluators, quality gates, and reporting."""

from __future__ import annotations

import pandas as pd
import pytest

from agentops_sdk.domain import exceptions
from agentops_sdk.evaluation import CompositeEvaluator
from agentops_sdk.evaluation import EvaluatorRegistry
from agentops_sdk.evaluation import HeuristicEvaluator
from agentops_sdk.evaluation import InMemoryEvaluator
from agentops_sdk.evaluation import QualityGateSessionReporter
from agentops_sdk.evaluation import QualityGateTestRecord
from agentops_sdk.evaluation import assert_quality_gate
from agentops_sdk.evaluation import assert_trajectory_quality_gate
from agentops_sdk.evaluation import build_suite_result
from agentops_sdk.evaluation import evaluate_agent_response
from agentops_sdk.evaluation import get_evaluator
from agentops_sdk.evaluation import reporting
from agentops_sdk.evaluation import use_session_reporter
from agentops_sdk.evaluation import validate_metric_code


def test_in_memory_evaluator_suite_and_trajectory() -> None:
    ev_mem = InMemoryEvaluator()
    suite = ev_mem.evaluate_suite(
        [{"prompt": "oil", "context": "15W-40 oil", "response": "15W-40 oil"}],
        thresholds={"groundedness": 0.5},
    )
    assert suite.all_passed is True
    assert (
        ev_mem.evaluate_trajectory([{"role": "user", "content": "hi"}])[
            "task_success"
        ]
        == 0.0
    )
    err_traj = ev_mem.evaluate_trajectory(
        [
            {"role": "user", "content": "help"},
            {"role": "assistant", "content": "Internal error occurred"},
        ]
    )
    assert err_traj["task_success"] == 0.30


def test_heuristic_evaluator_metrics_and_errors() -> None:
    heur = HeuristicEvaluator(
        metrics=(
            "exact_match",
            "keyword_coverage",
            "similarity",
            "json_validity",
            "length_ratio",
            "regex_match",
        ),
        regex_pattern=r"part",
        required_keywords=("part",),
        custom_rules={"const": lambda _item: 0.75},
    )
    scores = heur.evaluate(
        [{"response": '```json\n{"part": 1}\n```', "reference": '{"part": 1}'}]
    )
    assert scores["json_validity"] == 1.0 and scores["const"] == 0.75
    with pytest.raises(ValueError):
        HeuristicEvaluator(regex_pattern="[invalid")
    with pytest.raises(exceptions.EvaluationError):
        heur.evaluate([{"response": "ok"}], metrics=["unknown_metric"])


async def test_composite_aevaluate_and_registry() -> None:
    reg = EvaluatorRegistry()
    assert "in_memory" in reg.list_evaluators()
    assert reg.has("heuristic") is True
    reg.register("custom", InMemoryEvaluator(default_scores={"m": 0.9}))
    assert reg.get("custom").evaluate([{"response": "x"}]) == {"m": 0.9}
    comp = get_evaluator(["in_memory", "heuristic"], prefix_provider=True)
    assert isinstance(comp, CompositeEvaluator)
    row = [
        {
            "prompt": "p",
            "context": "ctx",
            "response": "ctx",
            "reference": "ctx",
        }
    ]
    out = comp.evaluate(row)
    assert "in_memory/groundedness" in out and "heuristic/exact_match" in out
    assert await comp.aevaluate(row) == out
    with pytest.raises(ValueError):
        CompositeEvaluator([])
    with pytest.raises(ValueError):
        reg.get("missing-provider")


def test_validate_metric_code_security_checks() -> None:
    ok_code = "def evaluate(row):\n    return 1.0 if row else 0.0\n"
    assert validate_metric_code(ok_code, required_fn="evaluate") == ok_code
    bad_snippets = (
        "",
        "def bad(:\n",
        "import os\ndef evaluate(r): return 1",
        "from subprocess import run\ndef evaluate(r): return 1",
        "def evaluate(r): return eval('1')",
        "def evaluate(r): return x.system('ls')",
        "def other(): return 1",
    )
    for bad in bad_snippets:
        with pytest.raises(ValueError):
            validate_metric_code(bad, required_fn="evaluate")


def test_quality_gates_pass_fail_and_edge_cases() -> None:
    rep = QualityGateSessionReporter()
    with use_session_reporter(rep):
        res = assert_quality_gate(
            [{"prompt": "oil", "context": "15W-40", "response": "15W-40"}],
            thresholds={"groundedness": 0.5},
            test_name="gate|pipe`test",
            console_url="https://console.cloud.google.com/run/1",
        )
        assert res.is_passing is True
        with pytest.raises(exceptions.QualityGateError) as err:
            assert_trajectory_quality_gate(
                [{"role": "user", "content": "hi"}],
                thresholds={"task_success": 0.9},
            )
        assert "task_success" in err.value.failed_metrics
        assert "Diagnostic Failure Analysis" in err.value.report_markdown
        for bad_kw in (
            {"evaluator": "unknown-ev"},
            {"metrics": []},
            {
                "thresholds": {"missing_metric": 0.5},
                "metrics": ["groundedness"],
            },
        ):
            with pytest.raises(exceptions.QualityGateError):
                evaluate_agent_response(
                    [{"prompt": "p", "context": "c", "response": "c"}],
                    **bad_kw,  # type: ignore[arg-type]
                )
    assert "gate\\|pipe'test" in rep.generate_markdown()


def test_reporting_markdown_sanitization_and_stats() -> None:
    rep = QualityGateSessionReporter()
    rep.add_record(
        QualityGateTestRecord(
            test_name="bad_link",
            eval_mode="pointwise",
            metrics={"groundedness": 0.9},
            thresholds={"groundedness": 0.8},
            is_passing=True,
            console_url="javascript:alert(1)",
        )
    )
    assert "javascript:" not in rep.generate_markdown()
    df = pd.DataFrame({"m/score": [4.0, 5.0], "m/explanation": ["ok", "great"]})
    norm, raw, expl = reporting.extract_metric_stats("m", {}, df, pd, True)
    assert raw == 4.5 and norm == 0.9 and expl == ["ok", "great"]
    suite = build_suite_result(
        [{"prompt": "q", "response": "a"}],
        {"m": 0.9},
        {"m": 4.5},
        table=df,
        expls={"m": ["ok"]},
        thresholds={"m": 0.8},
    )
    assert suite.all_passed is True
