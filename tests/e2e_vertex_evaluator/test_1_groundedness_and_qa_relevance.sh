#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 1: Groundedness & QA Relevance Scoring via VertexEvalTaskAdapter (v1)

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

evaluator = VertexEvalTaskAdapter(
    VertexAIEvalConfig(
        project_id="${PROJECT_ID}",
        location="${LOCATION}",
        metrics=["groundedness", "question_answering_relevance"],
        normalize_scores=True,
    )
)

grounded_dataset = [
    {
        "prompt": "What is the hydraulic oil capacity of the Series-800 equipment?",
        "context": "Service Manual Series-800: Hydraulic reservoir capacity is 125 liters.",
        "response": "The hydraulic reservoir capacity of the Series-800 equipment is 125 liters.",
    }
]
res_grounded = evaluator.evaluate(grounded_dataset)
print("Grounded Scores:", res_grounded)
assert res_grounded.get("groundedness", 0.0) >= 0.80
assert res_grounded.get("question_answering_relevance", 0.0) >= 0.80

hallucinated_dataset = [
    {
        "prompt": "What is the hydraulic oil capacity of the Series-800 equipment?",
        "context": "Service Manual Series-800: Hydraulic reservoir capacity is 125 liters.",
        "response": "The Series-800 equipment holds 999 gallons and never needs changing.",
    }
]
res_bad = evaluator.evaluate(hallucinated_dataset)
print("Hallucinated Scores:", res_bad)
assert res_bad.get("groundedness", 0.0) <= 0.50
print("[PASS] Groundedness and QA relevance scoring verified.")
PYEOF
