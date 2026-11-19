#!/usr/bin/env python3
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""ADK-style Parts & Service agent evaluated via VertexEvalTaskAdapter."""

from __future__ import annotations

import argparse
import json
import logging
import os
from typing import Any

from agentops_sdk import EvaluationResult
from agentops_sdk import InMemoryEvaluator
from agentops_sdk import VertexAIEvalConfig
from agentops_sdk import VertexEvalTaskAdapter
from agentops_sdk import evaluate_agent_response

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("adk-evaluator-agent")


class ADKPartsCatalogEvaluationAgent:
    """Parts & service agent evaluated via Vertex AI or InMemory evaluator."""

    def __init__(
        self,
        project_id: str,
        location: str = "europe-west4",
        eval_mode: str = "vertex",
    ) -> None:
        self.project_id = project_id
        self.location = location
        self.eval_mode = eval_mode
        if eval_mode == "in_memory":
            self.evaluator: Any = InMemoryEvaluator()
        else:
            cfg = VertexAIEvalConfig(
                project_id=self.project_id,
                location=self.location,
                metrics=["groundedness", "question_answering_relevance"],
                experiment="customer-adk-eval-agent",
                normalize_scores=True,
            )
            self.evaluator = VertexEvalTaskAdapter(config=cfg)

    def retrieve_context(self, query: str) -> str:
        """Simulate context from OEM Technical Bulletins."""
        catalog = {
            "hydraulic": (
                "OEM Technical Bulletin TB-2026-08: Series-800 "
                "equipment require OEM Fluid Plus hydraulic fluid. "
                "The main hydraulic filter part number is PART-H7042, "
                "replaced every 500 hours with the filter bowl tightened "
                "to 40 Nm."
            ),
            "combine": (
                "Harvester Service Manual: Rotor speed sensor air gap must be "
                "between 1.0 mm and 1.5 mm using feeler gauge TOOL-T99."
            ),
        }
        for key, text in catalog.items():
            if key in query.lower():
                return text
        return (
            "General Manual: Stop engine, engage parking brake, and disconnect "
            "battery master switch before service."
        )

    def generate_response(self, mode: str = "compliant") -> str:
        """Return candidate answer matching the test scenario."""
        if mode == "compliant":
            return (
                "Series-800 equipment require OEM Fluid Plus hydraulic "
                "fluid and hydraulic filter part number PART-H7042, "
                "replaced every 500 hours and tightened to 40 Nm."
            )
        if mode == "hallucinated":
            return (
                "Use any universal automotive engine oil with filter FAKE-9999 "
                "and replace every 10,000 hours."
            )
        if mode == "irrelevant":
            return "Tractor tires should be inflated to 22 PSI for road travel."
        raise ValueError(f"Unknown mode: {mode}")

    def evaluate_scenario(self, query: str, mode: str) -> dict[str, Any]:
        """Evaluate one generated response against quality thresholds."""
        context = self.retrieve_context(query)
        response = self.generate_response(mode)
        logger.info("Evaluating scenario '%s' (%s)...", mode, self.eval_mode)
        result: EvaluationResult = evaluate_agent_response(
            [{"prompt": query, "context": context, "response": response}],
            thresholds={
                "groundedness": 0.80,
                "question_answering_relevance": 0.80,
            },
            evaluator=self.evaluator,
            run_name=f"adk-{mode}",
            record_report=False,
        )
        return {
            "scenario": mode,
            "query": query,
            "context": context,
            "response": response,
            "groundedness_score": result.scores.get("groundedness", 0.0),
            "relevance_score": result.scores.get(
                "question_answering_relevance", 0.0
            ),
            "is_passing": result.is_passing,
            "explanations": result.explanations,
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        default=os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
    )
    parser.add_argument(
        "--location",
        default=os.environ.get("GOOGLE_CLOUD_LOCATION", "europe-west4"),
    )
    parser.add_argument(
        "--eval-mode",
        choices=("vertex", "in_memory"),
        default="vertex",
    )
    parser.add_argument(
        "--mode",
        choices=("all", "compliant", "hallucinated", "irrelevant"),
        default="all",
    )
    parser.add_argument("--json-output", action="store_true")
    return parser.parse_args()


def main() -> None:
    """Execute evaluation scenarios and print structured summary."""
    args = _parse_args()
    agent = ADKPartsCatalogEvaluationAgent(
        project_id=args.project,
        location=args.location,
        eval_mode=args.eval_mode,
    )
    query = (
        "What hydraulic fluid and filter part number fits Series-800 equipment?"
    )
    modes = (
        ["compliant", "hallucinated", "irrelevant"]
        if args.mode == "all"
        else [args.mode]
    )
    results = [agent.evaluate_scenario(query=query, mode=m) for m in modes]
    if args.json_output:
        print(json.dumps(results, indent=2))
    else:
        for res in results:
            verdict = "PASS" if res["is_passing"] else "BLOCKED"
            print(
                f"[{res['scenario']}] {verdict} | "
                f"grounded={res['groundedness_score']:.2f} "
                f"rel={res['relevance_score']:.2f}"
            )
    for res in results:
        if res["scenario"] == "compliant" and not res["is_passing"]:
            raise AssertionError(f"Expected compliant scenario to pass: {res}")
        if (
            res["scenario"] in {"hallucinated", "irrelevant"}
            and res["is_passing"]
        ):
            raise AssertionError(
                f"Expected {res['scenario']} scenario to fail: {res}"
            )


if __name__ == "__main__":
    main()
