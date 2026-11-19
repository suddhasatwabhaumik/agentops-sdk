# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Pytest quality gate compatible with --eval-mode=in_memory or vertex."""

from __future__ import annotations

from typing import Any

import pytest

from agentops_sdk import QualityGateError
from agentops_sdk import assert_quality_gate
from agentops_sdk.evaluation.reporting import session_reporter

CONTEXT = (
    "The Series-700 equipment uses oil filter PART-1001, "
    "changed every 500 hours."
)
QUESTION = "Which oil filter does a Series-700 need?"


def _thresholds_for(mode: str, config: pytest.Config) -> dict[str, float]:
    if mode == "heuristic":
        return {"keyword_coverage": 0.8, "similarity": 0.8}
    return {
        "groundedness": float(
            config.getoption(
                "--fail-under-groundedness",
                default=session_reporter.default_groundedness,
            )
        ),
        "question_answering_relevance": float(
            config.getoption(
                "--fail-under-relevance",
                default=session_reporter.default_relevance,
            )
        ),
    }


@pytest.mark.quality_gate
def test_grounded_answer_passes(
    quality_gate_evaluator: Any,
    eval_mode: str,
    pytestconfig: pytest.Config,
) -> None:
    """Verify grounded answer satisfies quality gate thresholds."""
    dataset = [
        {
            "prompt": QUESTION,
            "context": CONTEXT,
            "reference": CONTEXT,
            "response": (
                "Series-700 uses oil filter PART-1001, changed every 500 hours."
            ),
        }
    ]
    result = assert_quality_gate(
        dataset,
        thresholds=_thresholds_for(eval_mode, pytestconfig),
        evaluator=quality_gate_evaluator,
        test_name="series_700_grounded_answer",
    )
    assert result.is_passing


@pytest.mark.quality_gate
def test_hallucinated_answer_fails(
    quality_gate_evaluator: Any,
    eval_mode: str,
    pytestconfig: pytest.Config,
) -> None:
    """Verify hallucinated answer triggers QualityGateError."""
    dataset = [
        {
            "prompt": QUESTION,
            "context": CONTEXT,
            "reference": CONTEXT,
            "response": "It requires component X-999 replaced monthly.",
        }
    ]
    with pytest.raises(QualityGateError):
        assert_quality_gate(
            dataset,
            thresholds=_thresholds_for(eval_mode, pytestconfig),
            evaluator=quality_gate_evaluator,
            test_name="series_700_hallucinated_answer",
        )
