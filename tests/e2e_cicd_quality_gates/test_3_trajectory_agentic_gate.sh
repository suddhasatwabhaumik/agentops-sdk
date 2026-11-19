#!/usr/bin/env bash
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
#
# TEST 3: Multi-Turn Agentic Trajectory Quality Gate Verification (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}}"
LOCATION="${2:-europe-west4}"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

echo "Running Test 3: Multi-Turn Trajectory Quality Gates (${PROJECT_ID}/${LOCATION})..."

PROJECT_ID="${PROJECT_ID}" LOCATION="${LOCATION}" "${PYTHON_BIN}" - << 'PYEOF'
import os

from agentops_sdk import QualityGateError
from agentops_sdk import assert_trajectory_quality_gate
from agentops_sdk.evaluation import QualityGateSessionReporter
from agentops_sdk.evaluation import use_session_reporter

passing_traj = [
    {"role": "user", "content": "Combine overheating above 110C in field."},
    {
        "role": "agent",
        "content": "Querying telematics sensor readings.",
        "tool_calls": [{"name": "query_cooling_telemetry", "args": {"id": "C-942"}}],
    },
    {
        "role": "tool",
        "name": "query_cooling_telemetry",
        "content": '{"fan_rpm": 420, "expected_rpm": 1800}',
    },
    {
        "role": "agent",
        "content": "Viscous fan clutch slippage detected. Shut down and inspect PART-2214.",
    },
]

reporter = QualityGateSessionReporter()
reporter.configure(
    eval_mode="in_memory",
    project=os.environ["PROJECT_ID"],
    location=os.environ["LOCATION"],
)

with use_session_reporter(reporter):
    res = assert_trajectory_quality_gate(
        passing_traj,
        thresholds={"tool_use_quality": 0.80, "task_success": 0.80},
        evaluator="in_memory",
        record_report=True,
        test_name="adk_agent::trajectory_pass",
    )
    assert res.is_passing is True
    assert res.eval_mode == "trajectory"
    assert res.scores["tool_use_quality"] >= 0.80
    assert res.scores["task_success"] >= 0.80

failing_traj = [
    {"role": "user", "content": "Combine overheating above 110C in field."},
    {"role": "agent", "content": "Sorry, internal error and failed to connect."},
]
try:
    assert_trajectory_quality_gate(
        failing_traj,
        thresholds={"tool_use_quality": 0.80, "task_success": 0.80},
        evaluator="in_memory",
        record_report=False,
        test_name="adk_agent::trajectory_fail",
    )
    raise AssertionError("Expected QualityGateError for failing trajectory!")
except QualityGateError as exc:
    assert "task_success" in exc.failed_metrics

text = reporter.generate_markdown()
assert "trajectory" in text
assert "tool_use_quality" in text
assert "task_success" in text

print(f"  => Passing trajectory scores: {res.scores}; failing trajectory rejected cleanly.")
PYEOF

echo "[PASS] Test 3: Multi-Turn Trajectory Quality Gates succeeded."
