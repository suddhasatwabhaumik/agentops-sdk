# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Tutorial 13: evaluate responses via VertexEvalTaskAdapter."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared import gemini
from _shared import settings
from _shared import telemetry

cfg = settings.load("13_evaluation_quality_gates")
tel = telemetry.start(cfg)

from agentops_sdk import VertexAIEvalConfig
from agentops_sdk import VertexEvalTaskAdapter
from agentops_sdk import evaluate_agent_response
from agentops_sdk import trace_llm_call

CONTEXT = (
    "Service bulletin SB-700-031: the Series-700 (6.1L powertrain) "
    "uses oil filter PART-1001. Change interval 500 hours. "
    "Oil capacity 19 litres of 10W-40."
)
QUESTION = (
    "Which oil filter does a Series-700 need and how often is it changed?"
)


@trace_llm_call(
    kpi_id="service-answer-quality",
    pl_impact_line="aftersales-opex",
)
def answer(question: str, context: str):
    """Generate a grounded Gemini answer strictly from context."""
    return gemini.generate(
        f"Context:\n{context}\n\nQuestion: {question}",
        system="Answer only from the context, in one sentence.",
    )


def score(response: str, label: str) -> None:
    """Evaluate answer quality with Vertex AI Eval and print verdict."""
    eval_cfg = VertexAIEvalConfig(
        project_id=cfg.project,
        location=cfg.location,
        experiment="agentops-tutorial-eval",
        experiment_run_name=f"{label}-{cfg.run_id[-8:]}",
    )
    evaluator = VertexEvalTaskAdapter(config=eval_cfg)
    result = evaluate_agent_response(
        [{"prompt": QUESTION, "context": CONTEXT, "response": response}],
        thresholds={
            "groundedness": 0.8,
            "question_answering_relevance": 0.8,
        },
        evaluator=evaluator,
        run_name=f"{label}-{cfg.run_id[-8:]}",
    )
    print(
        f"\n{label}: passing={result.is_passing} "
        f"scores={result.scores} mode={result.eval_mode}"
    )
    for metric, text in result.explanations.items():
        print(f"   {metric}: {str(text)[:160]}")
    if result.console_url:
        print(f"   Vertex AI Experiments: {result.console_url}")


def main() -> None:
    """Evaluate a grounded Gemini answer and a hallucinated control answer."""
    good = answer(QUESTION, CONTEXT).text.strip()
    print(f"\nGemini: {good}")
    score(good, "grounded")
    score(
        "The Series-700 uses filter X-999 and the oil is changed every 50 h.",
        "hallucinated",
    )
    tel.flush()


if __name__ == "__main__":
    main()
