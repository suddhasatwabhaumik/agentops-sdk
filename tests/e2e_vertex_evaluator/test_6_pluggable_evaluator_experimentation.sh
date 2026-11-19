#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 6: Pluggable Evaluators & ExperimentEngine (v1)

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
from agentops_sdk import CompositeEvaluator
from agentops_sdk import ExperimentConfig
from agentops_sdk import ExperimentEngine
from agentops_sdk import HeuristicEvaluator
from agentops_sdk import VertexAIEvalConfig
from agentops_sdk import VertexEvalTaskAdapter
from agentops_sdk.evaluation import evaluator_registry

print("Registered Evaluators:", evaluator_registry.list_evaluators())

dataset = [
    {
        "prompt": "Check diagnostic code DTC-HYDR-401 for Series-800 equipment.",
        "context": (
            "Service Manual Series-800: DTC-HYDR-401 indicates low hydraulic flow "
            "from clogged filter Part #PART-HYD-8821. Replace filter and fill "
            "hydraulic oil reservoir to 125 liters."
        ),
        "response": (
            "DTC-HYDR-401 indicates low hydraulic flow on the Series-800. Replace "
            "filter element with Part #PART-HYD-8821 and fill reservoir to "
            "125 liters."
        ),
        "reference": (
            "DTC-HYDR-401: replace filter Part #PART-HYD-8821 and fill to 125 liters."
        ),
    }
]

variants = {
    "variant_expert": {"role": "Senior Master Technician"},
    "variant_concise": {"role": "Parts Advisor"},
}

vertex_eval = VertexEvalTaskAdapter(
    VertexAIEvalConfig(
        project_id="${PROJECT_ID}",
        location="${LOCATION}",
        experiment="customer-pluggable-eval-e2e",
        metrics=["groundedness", "question_answering_relevance"],
        normalize_scores=True,
    )
)

exp_vertex = ExperimentEngine(
    ExperimentConfig(
        name="customer-parts-experiment-vertex",
        prompt_template="Role: {{ role }}\nContext: {{ context }}\nQ: {{ prompt }}",
        variants=variants,
        dataset=dataset,
        metrics=["groundedness", "question_answering_relevance"],
        evaluator=vertex_eval,
        thresholds={"groundedness": 0.80},
    )
)
res_vertex = exp_vertex.run()
assert res_vertex["variant_expert"].is_passing is True
winner = exp_vertex.get_winner(res_vertex, "groundedness")
print("Vertex Experiment Winner:", winner)
table = exp_vertex.get_summary_table(res_vertex)
assert len(table) == 2

heuristic_eval = HeuristicEvaluator(
    metrics=["exact_match", "keyword_coverage", "similarity"],
    required_keywords=["PART-HYD-8821", "125", "liters", "filter"],
)
exp_heur = ExperimentEngine(
    ExperimentConfig(
        name="customer-parts-experiment-heuristic",
        prompt_template="Role: {{ role }}\nQ: {{ prompt }}",
        variants=variants,
        dataset=dataset,
        metrics=["keyword_coverage", "similarity"],
        evaluator=heuristic_eval,
        thresholds={"keyword_coverage": 0.80},
    )
)
res_heur = exp_heur.run()
assert res_heur["variant_expert"].scores["keyword_coverage"] == 1.0

composite = CompositeEvaluator(
    evaluators=[vertex_eval, heuristic_eval], prefix_provider=True
)
comp_scores = composite.evaluate(dataset, metrics=None)
assert "vertex_ai/groundedness" in comp_scores
assert "heuristic/keyword_coverage" in comp_scores
print("[PASS] Pluggable evaluators & ExperimentEngine verified.")
PYEOF
