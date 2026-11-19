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
# TEST 2: Google Customer Domain Golden Evaluation Benchmark Dataset (v1)

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

cd "${REPO_ROOT}"

echo "Running Test 2: Golden Benchmark Dataset Evaluation (${PROJECT_ID}/${LOCATION})..."

PROJECT_ID="${PROJECT_ID}" LOCATION="${LOCATION}" "${PYTHON_BIN}" - << 'PYEOF'
import os
from pathlib import Path
import yaml

from agentops_sdk import assert_quality_gate
from agentops_sdk.evaluation import QualityGateSessionReporter
from agentops_sdk.evaluation import use_session_reporter

project_id = os.environ["PROJECT_ID"]
location = os.environ["LOCATION"]
data = yaml.safe_load(Path("tests/data/golden_eval_dataset.yaml").read_text())
cases = data["test_cases"]
reporter = QualityGateSessionReporter()
reporter.configure(
    eval_mode="in_memory", project=project_id, location=location
)

with use_session_reporter(reporter):
    for case in cases:
        row = {
            "prompt": case["prompt"],
            "context": case["context"],
            "response": case["expected_response"],
        }
        res = assert_quality_gate(
            [row],
            thresholds={
                "groundedness": float(case["groundedness_threshold"]),
                "question_answering_relevance": float(
                    case["relevance_threshold"]
                ),
            },
            evaluator="in_memory",
            record_report=True,
            test_name=f"golden::{case['id']}",
        )
        assert res.is_passing, f"Golden case {case['id']} failed: {res.failed_metrics}"
        print(f"  => {case['id']}: {res.scores}")

text = reporter.generate_markdown()
assert project_id in text
for case in cases:
    assert f"golden::{case['id']}" in text
assert "(3/3)" in text

print("  => 3/3 Golden benchmark cases passed and Markdown report verified.")
PYEOF

echo "[PASS] Test 2: Google Customer Domain Golden Evaluation Benchmark succeeded."
