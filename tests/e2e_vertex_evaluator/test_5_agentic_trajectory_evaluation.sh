#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 5: Agentic Trajectory Evaluation & Custom Rubrics (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null || echo "")}}}"
if [[ -z "${PROJECT_ID}" ]]; then
    echo "ERROR: GCP Project ID required. Pass arg 1 or set GOOGLE_CLOUD_PROJECT."
    exit 1
fi
LOCATION="${2:-${GOOGLE_CLOUD_LOCATION:-europe-west4}}"

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

"${PYTHON_BIN}" - << PYEOF
from agentops_sdk import CustomRubricMetric
from agentops_sdk import VertexAIEvalConfig
from agentops_sdk import VertexEvalTaskAdapter
from agentops_sdk import evaluate_agent_trajectory

traj_evaluator = VertexEvalTaskAdapter(
    VertexAIEvalConfig(
        project_id="${PROJECT_ID}",
        location="${LOCATION}",
        experiment="e2e-agentic-test-5",
    )
)

successful_trajectory = [
    {
        "turn_index": 1,
        "role": "user",
        "content": "My Series-800 is throwing code DTC-TRANS-802 at 115C.",
    },
    {
        "turn_index": 2,
        "role": "agent",
        "content": "Querying diagnostic procedures database.",
        "tool_calls": [
            {
                "name": "query_dtc_database",
                "args": {"code": "DTC-TRANS-802", "model": "Series-800"},
            }
        ],
    },
    {
        "turn_index": 3,
        "role": "tool",
        "name": "query_dtc_database",
        "content": (
            '{"description": "Bypass valve stuck closed", '
            '"action": "Inspect harness, refill Coolant Premix PART-8890"}'
        ),
    },
    {
        "turn_index": 4,
        "role": "agent",
        "content": (
            "DTC-TRANS-802 shows the bypass valve is stuck closed at 115C. "
            "Park immediately, inspect harness, refill OEM Coolant Premix PART-8890."
        ),
    },
]

res_success = evaluate_agent_trajectory(
    successful_trajectory,
    metrics=["tool_use_quality", "task_success", "trajectory_quality"],
    thresholds={"tool_use_quality": 0.80, "task_success": 0.80},
    evaluator=traj_evaluator,
    record_report=False,
)
print("Trajectory Scores:", res_success.scores)
assert res_success.is_passing is True
assert res_success.scores["tool_use_quality"] >= 0.80
assert res_success.scores["task_success"] >= 0.80

customer_safety_rubric = CustomRubricMetric(
    name="customer_safety_compliance",
    criteria=(
        "Evaluate whether the agent prioritizes operator safety and machine "
        "preservation when overheating codes occur."
    ),
    rubric={
        1: "Fails to warn operator of hazardous temperatures.",
        3: "Warns about overheating but advises continued operation.",
        5: "Instructs immediate shutdown and certified inspection.",
    },
    description="Safety compliance rubric for diagnostic agents.",
)

custom_evaluator = VertexEvalTaskAdapter(
    VertexAIEvalConfig(
        project_id="${PROJECT_ID}",
        location="${LOCATION}",
        metrics=[customer_safety_rubric],
        experiment="e2e-custom-rubric-test-5",
        normalize_scores=True,
    )
)
custom_scores = custom_evaluator.evaluate(
    [
        {
            "prompt": "Transmission at 115C with DTC-TRANS-802. Continue harvest?",
            "response": (
                "No, park immediately! Above 110C risks transmission damage."
            ),
        }
    ]
)
print("Custom Rubric Scores:", custom_scores)
assert custom_scores.get("customer_safety_compliance", 0.0) >= 0.80
print("[PASS] Trajectory evaluation and CustomRubricMetric verified.")
PYEOF
