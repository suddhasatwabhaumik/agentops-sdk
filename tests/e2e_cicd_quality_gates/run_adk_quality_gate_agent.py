#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""ADK-style diagnostics agent integrated with v1 CI/CD Quality Gates."""

from __future__ import annotations

import argparse
import logging
import os
from typing import Any

from opentelemetry import trace

from agentops_sdk import EvaluationResult
from agentops_sdk import QualityGateError
from agentops_sdk import assert_quality_gate
from agentops_sdk import assert_trajectory_quality_gate
from agentops_sdk import configure_telemetry
from agentops_sdk import get_evaluator
from agentops_sdk import reset_telemetry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("adk-quality-gate-agent")
tracer = trace.get_tracer("adk-quality-gate-agent")


class ADKEquipmentDiagnosticsAgent:
    """Equipment diagnostics agent gated by v1 evaluation quality gates."""

    def __init__(
        self,
        project_id: str | None = None,
        location: str = "europe-west4",
        eval_mode: str = "in_memory",
    ) -> None:
        self.project_id = (
            project_id
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("GCP_PROJECT")
            or ""
        )
        self.location = location
        self.eval_mode = eval_mode
        if eval_mode == "vertex":
            self.evaluator = get_evaluator(
                "vertex_ai", project_id=self.project_id, location=self.location
            )
        else:
            self.evaluator = get_evaluator(eval_mode)

    def run_clean_inquiry(self) -> dict[str, str]:
        """Return a grounded single-turn inquiry sample."""
        with tracer.start_as_current_span("run_clean_inquiry"):
            return {
                "prompt": (
                    "What coolant specification is required for Series-700 "
                    "7.4L engines?"
                ),
                "context": (
                    "Engine Tech Spec: Series-700 7.4L engines require OAT "
                    "(Organic Acid Technology) coolant meeting OEM "
                    "specification SPEC-1145. Mix 50/50 with demineralized "
                    "water."
                ),
                "response": (
                    "Series-700 7.4L engines require OAT coolant meeting OEM "
                    "specification SPEC-1145, mixed 50/50 with demineralized "
                    "water."
                ),
            }

    def run_hallucinated_inquiry(self) -> dict[str, str]:
        """Return an ungrounded hallucinated sample."""
        with tracer.start_as_current_span("run_hallucinated_inquiry"):
            return {
                "prompt": (
                    "What coolant specification is required for Series-700 "
                    "7.4L engines?"
                ),
                "context": (
                    "Engine Tech Spec: Series-700 7.4L engines require OAT "
                    "coolant meeting OEM specification SPEC-1145."
                ),
                "response": (
                    "Any green ethylene glycol or unfiltered well water works."
                ),
            }

    def run_clean_trajectory(self) -> list[dict[str, Any]]:
        """Return a compliant multi-turn trajectory with tool calls."""
        with tracer.start_as_current_span("run_clean_trajectory"):
            return [
                {
                    "role": "user",
                    "content": "Combine overheating above 110C in the field.",
                },
                {
                    "role": "agent",
                    "content": "Checking telematics sensor readings now.",
                    "tool_calls": [
                        {
                            "name": "query_cooling_telemetry",
                            "args": {"machine_id": "COMBINE-942"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "name": "query_cooling_telemetry",
                    "content": '{"fan_rpm": 420, "expected_rpm": 1800}',
                },
                {
                    "role": "agent",
                    "content": (
                        "Viscous fan clutch slippage detected (420 vs 1800 RPM)"
                        ". Idle 3 minutes, shut down, inspect PART-2214."
                    ),
                },
            ]

    def run_failing_trajectory(self) -> list[dict[str, Any]]:
        """Return a failing trajectory that omits tool inspection."""
        with tracer.start_as_current_span("run_failing_trajectory"):
            return [
                {
                    "role": "user",
                    "content": "Combine overheating above 110C in the field.",
                },
                {
                    "role": "agent",
                    "content": "Sorry, internal error; failed to connect.",
                },
            ]

    def verify_single_turn_gate(
        self,
        prompt: str,
        context: str,
        response: str,
        thresholds: dict[str, float] | None = None,
    ) -> EvaluationResult:
        """Evaluate single-turn payload against v1 quality thresholds."""
        default_thresh = (
            {"keyword_coverage": 0.80, "similarity": 0.80}
            if self.eval_mode == "heuristic"
            else {"groundedness": 0.85, "question_answering_relevance": 0.80}
        )
        return assert_quality_gate(
            [{"prompt": prompt, "context": context, "response": response}],
            thresholds=thresholds or default_thresh,
            evaluator=self.evaluator,
            record_report=False,
            test_name="adk_agent::single_turn",
        )

    def verify_trajectory_gate(
        self,
        trajectory: list[dict[str, Any]],
        thresholds: dict[str, float] | None = None,
    ) -> EvaluationResult:
        """Evaluate multi-turn trajectory against v1 quality thresholds."""
        return assert_trajectory_quality_gate(
            trajectory,
            thresholds=thresholds
            or {"tool_use_quality": 0.80, "task_success": 0.80},
            evaluator=self.evaluator,
            record_report=False,
            test_name="adk_agent::trajectory",
        )


def _run_scenarios(agent: ADKEquipmentDiagnosticsAgent, mode: str) -> None:
    if mode in ("clean_single_turn", "all"):
        inq = agent.run_clean_inquiry()
        res = agent.verify_single_turn_gate(
            inq["prompt"], inq["context"], inq["response"]
        )
        logger.info("Clean single-turn PASS: %s", res.scores)

    if mode in ("hallucinated_single_turn", "all"):
        inq = agent.run_hallucinated_inquiry()
        try:
            agent.verify_single_turn_gate(
                inq["prompt"], inq["context"], inq["response"]
            )
            raise AssertionError("Expected QualityGateError on hallucination")
        except QualityGateError as exc:
            logger.info("Caught QualityGateError: %s", exc.failed_metrics)

    if mode in ("clean_trajectory", "all"):
        res = agent.verify_trajectory_gate(agent.run_clean_trajectory())
        logger.info("Clean trajectory PASS: %s", res.scores)

    if mode in ("failing_trajectory", "all"):
        try:
            agent.verify_trajectory_gate(agent.run_failing_trajectory())
            raise AssertionError("Expected QualityGateError on failing traj")
        except QualityGateError as exc:
            logger.info("Caught QualityGateError: %s", exc.failed_metrics)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-id",
        default=os.getenv("GOOGLE_CLOUD_PROJECT", ""),
    )
    parser.add_argument(
        "--location",
        default=os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west4"),
    )
    parser.add_argument(
        "--eval-mode",
        choices=("in_memory", "heuristic", "vertex"),
        default="in_memory",
    )
    parser.add_argument(
        "--mode",
        choices=(
            "clean_single_turn",
            "hallucinated_single_turn",
            "clean_trajectory",
            "failing_trajectory",
            "all",
        ),
        default="all",
    )
    return parser.parse_args()


def main() -> None:
    """Run requested quality-gate scenarios."""
    args = _parse_args()
    configure_telemetry(mode="off", service_name="adk-quality-gate-e2e")
    try:
        agent = ADKEquipmentDiagnosticsAgent(
            project_id=args.project_id,
            location=args.location,
            eval_mode=args.eval_mode,
        )
        _run_scenarios(agent, args.mode)
        print("ADK Agent Quality Gate checks completed with 0 errors.")
    finally:
        reset_telemetry()


if __name__ == "__main__":
    main()
