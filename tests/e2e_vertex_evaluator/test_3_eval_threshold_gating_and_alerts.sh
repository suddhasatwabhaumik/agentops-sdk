#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 3: Quality Gate Threshold Enforcement & Verdicts (v1)

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
    VertexAIEvalConfig(project_id="${PROJECT_ID}", location="${LOCATION}")
)
prompt = "What coolant specification is required for Series-700 7.4L engines?"
context = (
    "Engine Tech Spec: Series-700 7.4L engines require OAT coolant meeting "
    "OEM specification SPEC-1145. Mix 50/50 with demineralized water."
)
thresholds = {"groundedness": 0.80, "question_answering_relevance": 0.80}

res_ok = evaluate_agent_response(
    [
        {
            "prompt": prompt,
            "context": context,
            "response": (
                "Series-700 7.4L engines require OAT coolant meeting SPEC-1145, "
                "mixed 50/50 with demineralized water."
            ),
        }
    ],
    thresholds=thresholds,
    evaluator=evaluator,
    record_report=False,
)
assert res_ok.is_passing is True
assert res_ok.scores["groundedness"] >= 0.80
assert res_ok.scores["question_answering_relevance"] >= 0.80

res_bad = evaluate_agent_response(
    [{"prompt": prompt, "context": context, "response": "Pure tap water works."}],
    thresholds=thresholds,
    evaluator=evaluator,
    record_report=False,
)
assert res_bad.is_passing is False

res_irrel = evaluate_agent_response(
    [
        {
            "prompt": prompt,
            "context": context,
            "response": "Inspect cab air filter weekly during harvest season.",
        }
    ],
    thresholds=thresholds,
    evaluator=evaluator,
    record_report=False,
)
assert res_irrel.is_passing is False
print("[PASS] Quality gate threshold enforcement verified.")
PYEOF
