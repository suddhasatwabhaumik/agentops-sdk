# 13 · Evaluation & Quality Gates: Vertex AI Scores Answers, CI Blocks Regressions (`v1.0.0`)

**What you learn:** how to measure whether an agent answer is supported by its sources (`groundedness`) and on-topic (`question_answering_relevance`) via `VertexEvalTaskAdapter`, and how to enforce CI quality gates with the `agentops_eval` pytest plugin.

```
question + context ──► Gemini ──► response
                                    │
evaluate_agent_response(dataset, thresholds={"groundedness": 0.8, ...}, evaluator=VertexEvalTaskAdapter(config))
                                    │
                     Vertex AI EvalTask (global judge_model="gemini-2.5-flash") ──► 0.0..1.0 normalized scores
                                    │                                             └─► Vertex AI Experiments run
                     EvaluationResult(is_passing, scores, failed_metrics, explanations, console_url)
pytest: assert_quality_gate(...) raises QualityGateError when thresholds fail
```

## SDK calls used

| Call | What it does |
|---|---|
| `VertexAIEvalConfig(project_id, location, experiment, experiment_run_name, judge_location="global", judge_model="gemini-2.5-flash")` | Configures `VertexEvalTaskAdapter` and experiment tracking |
| `VertexEvalTaskAdapter(config=eval_cfg)` | `EvaluatorPort` adapter wrapping Vertex AI `EvalTask` (raises `EvaluationError` on empty inputs or service failures—zero silent `0.0` masking) |
| `evaluate_agent_response(dataset, *, thresholds=..., evaluator=..., run_name=...)` | Evaluates one or more `{prompt, context, response}` items into an `EvaluationResult` |
| `assert_quality_gate(dataset, thresholds=..., ...)` | Evaluates and raises `QualityGateError` if any metric falls below threshold; respects pytest `--eval-mode` |
| `evaluate_agent_trajectory` / `assert_trajectory_quality_gate` | Multi-turn tool-use trajectory evaluation (`[]` raises `EvaluationError`) |
| `InMemoryEvaluator`, `HeuristicEvaluator` | Explicit offline evaluators (`--eval-mode=in_memory` / `heuristic`) for fast CI unit suites |

## GCP resources used

| Resource | Why | Access needed |
|---|---|---|
| Vertex AI Gen AI Evaluation (`location` for experiments, `judge_location="global"` for `gemini-2.5-flash`) | Autorater scoring | `roles/aiplatform.user` |
| Vertex AI Experiments (`agentops-tutorial-eval`) | Logs evaluation runs | `roles/aiplatform.user` |
| Vertex AI, Gemini | Produces candidate responses | `roles/aiplatform.user` |

## Run

```bash
python examples/tutorial/13_evaluation_quality_gates/main.py
pytest examples/tutorial/13_evaluation_quality_gates -q --eval-mode=vertex --eval-project=$GOOGLE_CLOUD_PROJECT --eval-location=europe-west4

# Offline CI sanity check (zero GCP calls):
pytest examples/tutorial/13_evaluation_quality_gates -q --eval-mode=in_memory
```

Expected: `grounded: passing=True` and `hallucinated: passing=False` with autorater explanations and Vertex Experiment `console_url`; pytest passes `2` tests (`test_quality_gate.py`). Remove generated `eval_quality_gate_summary.md` afterwards if not needed.
