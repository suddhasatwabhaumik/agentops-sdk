#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 2: Multi-Metric EvalTask Suite Execution (v1)

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
from agentops_sdk import VertexAIEvalConfig
from agentops_sdk import VertexEvalTaskAdapter
from agentops_sdk import evaluate_agent_response

evaluator = VertexEvalTaskAdapter(
    VertexAIEvalConfig(
        project_id="${PROJECT_ID}",
        location="${LOCATION}",
        metrics=["groundedness", "question_answering_relevance", "safety"],
        experiment="multi-metric-test",
        normalize_scores=True,
    )
)

batch_dataset = [
    {
        "prompt": "What is the recommended tyre pressure for field operations?",
        "context": "Manual 4.1: Field operations require 1.2 bar (17.4 PSI).",
        "response": "Maintain tyre pressure at 1.2 bar (17.4 PSI) for field work.",
    },
    {
        "prompt": "How often should engine oil be changed?",
        "context": "Schedule: Replace engine oil and filter every 600 operating hours.",
        "response": "Engine oil and filter must be replaced every 600 hours.",
    },
]

result = evaluate_agent_response(
    batch_dataset,
    metrics=["groundedness", "question_answering_relevance", "safety"],
    thresholds={
        "groundedness": 0.70,
        "question_answering_relevance": 0.70,
        "safety": 0.70,
    },
    evaluator=evaluator,
    record_report=False,
)
print("Aggregate Scores:", result.scores)
for metric in ("groundedness", "question_answering_relevance", "safety"):
    val = result.scores[metric]
    assert 0.0 <= val <= 1.0
print("[PASS] Multi-metric Vertex evaluation verified.")
PYEOF
