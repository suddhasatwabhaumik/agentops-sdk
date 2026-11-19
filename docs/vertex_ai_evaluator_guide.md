# AgentOps Evaluation Framework: Developer Guide & API Reference (`v1.0.0`)

```
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
```

## 1. Executive Summary

`agentops_sdk.evaluation` delivers a modular, port-based evaluation suite built around `EvaluatorPort` and `ExperimentTrackerPort` (`agentops_sdk.ports.evaluation`):

1. **Google Cloud Vertex AI Evaluation (`VertexEvalTaskAdapter`, `[eval]` extra)**: Wraps `vertexai.evaluation.EvalTask` and `vertexai._genai` evaluation APIs with normalized `0.0–1.0` scoring, experiment tracking, and Cloud Console deep links (`judge_location="global"`, `judge_model="gemini-2.5-flash"` default).
2. **Pluggable Multi-Evaluator Registry (`EvaluatorRegistry`)**: Supports built-in keys `"in_memory"`, `"heuristic"`, `"ragas"`, `"vertex"`, `"vertex_ai"`, and `"composite"`, plus runtime registration of custom `EvaluatorPort` classes.
3. **Explicit Fail-Loud Execution (Zero Silent Mock Fallbacks)**: Offline CI suites explicitly select `InMemoryEvaluator` (`evaluator="in_memory"`) or `HeuristicEvaluator` (`evaluator="heuristic"`). Cloud evaluators (`VertexEvalTaskAdapter`, `genai_evals`, `metric_registry`, `simulator`) raise `EvaluationError` cleanly on empty inputs or service failures instead of silently returning `0.0` or mock outputs.
4. **Gen AI Evaluation Extensions**: Scenario generation (`generate_conversation_scenarios`), user simulation (`simulate_agent_conversations`), AST-guarded custom code-execution metrics (`register_code_execution_metric`), custom LLM judge metrics (`register_llm_metric`), automatic failure clustering (`generate_loss_clusters`), and managed Agent Engine evaluation (`evaluate_agent_engine`).

### Evaluator Architecture

```text
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                EvaluatorRegistry                                        │
│    Built-in keys: "in_memory", "heuristic", "ragas", "vertex", "vertex_ai", "composite" │
└────────────────────────────────────────────┬────────────────────────────────────────────┘
                                             │
                                             ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                           EvaluatorPort (typing.Protocol)                               │
│  • provider_name -> str                                                                 │
│  • supported_metrics -> tuple[str, ...] | list[str]                                     │
│  • evaluate(dataset, *, metrics=None, **kwargs) -> dict[str, float]                     │
│  • evaluate_trajectory(trajectory, *, metrics=None, **kwargs) -> dict[str, float]       │
└────────────────────────────────────────────┬────────────────────────────────────────────┘
                                             │
        ┌──────────────────┬─────────────────┼──────────────────┬──────────────────┐
        ▼                  ▼                 ▼                  ▼                  ▼
┌────────────────┐ ┌────────────────┐ ┌───────────────┐ ┌────────────────┐ ┌───────────────┐
│  VertexEval    │ │     Ragas      │ │   Heuristic   │ │   InMemory     │ │   Composite   │
│  TaskAdapter   │ │   Evaluator    │ │   Evaluator   │ │   Evaluator    │ │   Evaluator   │
├────────────────┤ ├────────────────┤ ├───────────────┤ ├────────────────┤ ├───────────────┤
│ • Groundedness │ │ • Faithfulness │ │ • Exact Match │ │ • Deterministic│ │ • Multi-eval  │
│ • QA Relevance │ │ • Answer Rel   │ │ • Keywords    │ │   baseline     │ │   orchestrator│
│ • Trajectory   │ │ • Context Rec  │ │ • Similarity  │ │   unit scores  │ │ • Score merge │
│ • CustomRubric │ │ • [ragas] extra│ │ • custom_rules│ │ • Zero network │ │ • Prefixing   │
└────────────────┘ └────────────────┘ └───────────────┘ └────────────────┘ └───────────────┘
```

---

## 2. Quick-Reference Matrix

| Symbol | Registry Key | Environment | Key Capabilities |
| :--- | :--- | :--- | :--- |
| **`evaluate_agent_response`** | Default `"in_memory"` | Offline / Online | Evaluates prompt/context/response datasets against `thresholds` |
| **`evaluate_agent_trajectory`** | Default `"in_memory"` | Offline / Online | Evaluates multi-turn agent trajectories (raises `EvaluationError` on `[]`) |
| **`assert_quality_gate` / `assert_trajectory_quality_gate`** | Default `"in_memory"` | Offline / Online | Enforces quality thresholds and raises `QualityGateError` on failure |
| **`InMemoryEvaluator`** | `"in_memory"` | **Offline** | Zero-dependency deterministic unit-test & PR CI evaluator |
| **`HeuristicEvaluator`** | `"heuristic"` | **Offline** | Exact match, keyword coverage, similarity, JSON validity, regex, & `custom_rules` |
| **`RagasEvaluator`** | `"ragas"` | `[ragas]` / Heuristic | Faithfulness, answer relevancy, context recall via RAGAS |
| **`VertexEvalTaskAdapter`** | `"vertex"` / `"vertex_ai"` | **Online (`[eval]`)** | Live `EvalTask`, `evaluate_suite()`, `CustomRubricMetric`, Experiment console links |
| **`CompositeEvaluator`** | `"composite"` | **Hybrid** | Aggregates scores across multiple `EvaluatorPort` implementations |
| **`ExperimentEngine`** | — | Offline / Online | Renders Sandboxed Jinja2 prompt variants (`ExperimentConfig`) & tracks runs |
| **`generate_conversation_scenarios` / `simulate_agent_conversations`** | — | **Online** | Synthetic multi-turn user scenario generation & turn simulation (`Vertex AI`) |
| **`register_code_execution_metric` / `register_llm_metric`** | — | **Online** | Registers AST-validated Python (`validate_metric_code`) or LLM judge metrics |
| **`generate_loss_clusters`** | — | **Online** | Groups evaluation failures into semantic loss taxonomy clusters |
| **`evaluate_agent_engine`** | — | **Online** | Runs managed evaluation jobs against deployed Agent Engine endpoints |

---

## 3. Core Evaluation Runners & Adapters

### 3.1 `evaluate_agent_response`
Evaluates one or more `{prompt, context, response}` items against threshold SLAs. Pass `evaluator="in_memory"` (default), `"heuristic"`, or a configured `VertexEvalTaskAdapter(...)`.

```python
from agentops_sdk.evaluation import (
    VertexAIEvalConfig,
    VertexEvalTaskAdapter,
    evaluate_agent_response,
)

evaluator = VertexEvalTaskAdapter(
    config=VertexAIEvalConfig(
        project_id="<your-gcp-project-id>",
        location="europe-west4",
        experiment="parts-quality-ci",
    )
)

dataset = [
    {
        "prompt": "What is the torque specification for wheel bolts on Series-800?",
        "context": "Service Bulletin S800-04: Tighten wheel bolts in crisscross pattern to 450 Nm.",
        "response": "Series-800 wheel bolts must be tightened in a crisscross pattern to 450 Nm.",
    }
]

result = evaluate_agent_response(
    dataset,
    thresholds={"groundedness": 0.85, "question_answering_relevance": 0.80},
    evaluator=evaluator,
)

if not result.is_passing:
    raise ValueError(f"Quality gate failed: {result.failed_metrics}")

print(f"Groundedness: {result.scores['groundedness']:.2f}")
print(f"Console URL:  {result.console_url}")
```

**`EvaluationResult` fields**: `scores: dict[str, float]`, `raw_scores: dict[str, float]`, `is_passing: bool`, `failed_metrics: dict[str, tuple[float, float]]`, `explanations: dict[str, str]`, `eval_mode: str`, `run_name: str`, `console_url: str`.

### 3.2 `evaluate_agent_trajectory`
Evaluates multi-turn conversation and tool-use trajectories (`[]` raises `EvaluationError`):

```python
from agentops_sdk.evaluation import evaluate_agent_trajectory

trajectory = [
    {"role": "user", "content": "Equipment shows DTC-TRANS-802 and oil temp is 115C."},
    {
        "role": "agent",
        "content": "Checking DTC lookup tool.",
        "tool_calls": [{"name": "lookup_dtc", "args": {"code": "DTC-TRANS-802"}}],
    },
    {"role": "tool", "name": "lookup_dtc", "content": "Bypass valve stuck. Limit 110C."},
    {
        "role": "agent",
        "content": "Shut down equipment immediately and inspect OEM Coolant PART-8890.",
    },
]

result = evaluate_agent_trajectory(
    trajectory,
    thresholds={"tool_use_quality": 0.80, "task_success": 0.80},
    metrics=["tool_use_quality", "task_success"],
    evaluator="in_memory",
)
print(f"Tool Use: {result.scores['tool_use_quality']:.2f}, Passing: {result.is_passing}")
```

### 3.3 `VertexEvalTaskAdapter` & `evaluate_suite()`
```python
from agentops_sdk.evaluation import VertexAIEvalConfig, VertexEvalTaskAdapter

config = VertexAIEvalConfig(
    project_id="<your-gcp-project-id>",
    location="europe-west4",
    judge_location="global",
    judge_model="gemini-2.5-flash",
    experiment="diagnostics-qa-v1",
    metrics=["groundedness", "question_answering_quality"],
)

evaluator = VertexEvalTaskAdapter(config=config)

dataset = [
    {
        "prompt": "How do I check transmission fluid?",
        "context": "Check dipstick on right side of transmission case with engine idling.",
        "response": "Use the dipstick on the right side of the transmission case while idling.",
    },
    {
        "prompt": "What is the hydraulic filter part number?",
        "context": "Hydraulic return filter element is PART-5892.",
        "response": "The hydraulic return filter is PART-5892.",
    },
]

suite_result = evaluator.evaluate_suite(
    dataset,
    thresholds={"groundedness": 0.85, "question_answering_quality": 0.80},
)
print(f"Passed: {suite_result.passed_instances}/{suite_result.total_instances}")
print(f"Summary: {suite_result.summary_scores}")
print(f"Console: {suite_result.console_url}")
```

### 3.4 `HeuristicEvaluator` (Offline Metrics & `custom_rules`)
`HeuristicEvaluator` supports built-in deterministic metrics plus arbitrary `custom_rules` callables (replacing legacy separate custom-provider classes):

```python
from agentops_sdk.evaluation import HeuristicEvaluator


def has_disclaimer(sample: dict) -> float:
    return 1.0 if "DISCLAIMER" in str(sample.get("response", "")).upper() else 0.0


evaluator = HeuristicEvaluator(
    metrics=["exact_match", "keyword_coverage", "similarity", "json_validity"],
    required_keywords=["torque", "450 nm", "crisscross"],
    custom_rules={"has_disclaimer": has_disclaimer},
)

scores = evaluator.evaluate(
    [
        {
            "prompt": "Give torque specs",
            "reference": "Tighten wheel bolts in crisscross pattern to 450 Nm.",
            "response": "Tighten bolts in a crisscross pattern to 450 Nm. DISCLAIMER: OEM guide.",
        }
    ]
)
print("Heuristic Scores:", scores)
```

Available metrics: `exact_match`, `keyword_coverage`, `similarity`, `json_validity`, `length_ratio`, `regex_match` (requires `regex_pattern`), and any keys supplied in `custom_rules`.

### 3.5 `InMemoryEvaluator`, `RagasEvaluator`, `CompositeEvaluator` & `EvaluatorRegistry`
```python
from agentops_sdk.evaluation import (
    CompositeEvaluator,
    EvaluatorRegistry,
    InMemoryEvaluator,
    RagasEvaluator,
    get_evaluator,
)

# 1. Discover built-in registry keys
print(EvaluatorRegistry.list_evaluators())
# ['composite', 'heuristic', 'in_memory', 'ragas', 'vertex', 'vertex_ai']

# 2. Deterministic offline baseline evaluator
mem_eval = InMemoryEvaluator(default_scores={"groundedness": 0.92})

# 3. RAGAS evaluator (install [ragas] extra for full ragas metrics)
ragas_eval = RagasEvaluator(ragas_metrics=["faithfulness", "answer_relevancy"])

# 4. Composite multi-evaluator
composite = CompositeEvaluator(
    evaluators=[
        get_evaluator("in_memory"),
        get_evaluator("heuristic", metrics=["keyword_coverage", "json_validity"]),
    ],
    prefix_provider=False,
)
merged = composite.evaluate([{"prompt": "P", "context": "C", "response": "C"}])
```

---

## 4. Gen AI Evals, Simulation & Custom Metric Registry (`evaluation/genai_evals.py`, `metric_registry.py`, `simulator.py`)

### 4.1 Multi-Turn Scenario Generation & Simulation
```python
from agentops_sdk.evaluation import (
    UserScenarioGenerationConfig,
    UserSimulatorConfig,
    generate_conversation_scenarios,
    simulate_agent_conversations,
)

agent_info = {
    "name": "customer_diagnostic_agent",
    "root_agent_id": "diag_root",
    "instruction": "Assist technicians with Series-700 & Series-800 fault codes.",
}

gen_cfg = UserScenarioGenerationConfig(
    count=3,
    generation_instruction="Generate realistic heavy equipment fault inquiries.",
    environment_context="Heavy equipment diagnostics, hydraulic systems, and CAN-bus fault codes.",
)

scenarios = generate_conversation_scenarios(
    agent_info, gen_cfg, project_id="<your-gcp-project-id>", location="europe-west4"
)

def diagnostic_agent(prompt: str) -> str:
    return f"Inspect hydraulic sensor harness for query: {prompt}"

trajectories = simulate_agent_conversations(
    scenarios,
    diagnostic_agent,
    simulator_config=UserSimulatorConfig(max_turns=3),
    project_id="<your-gcp-project-id>",
    location="europe-west4",
)
```

### 4.2 AST-Guarded Custom Metrics (`register_code_execution_metric`, `register_llm_metric`)
`register_code_execution_metric()` validates submitted Python source with strict AST checking (`validate_metric_code`), blocking arbitrary imports, `eval`/`exec`, file/network operations, and dunder access outside the allowlist (`math`, `re`, `json`, `statistics`, `collections`, `itertools`, `functools`, `string`, `typing`, `decimal`, `fractions`, `difflib`):

```python
import vertexai
from vertexai._genai import types as vtypes

from agentops_sdk.evaluation import (
    delete_registered_metric,
    get_registered_metric,
    list_registered_metrics,
    register_code_execution_metric,
    register_llm_metric,
)

python_code = """
import re

def evaluate(instance):
    resp = str(instance.get("response", ""))
    return 1.0 if re.search(r"PART-[A-Z0-9-]+", resp) else 0.0
"""

code_info = register_code_execution_metric(
    metric_name="has_valid_oem_pn",
    python_code=python_code,
    display_name="Has Valid OEM Part Number",
    project_id="<your-gcp-project-id>",
    location="europe-west4",
)

llm_info = register_llm_metric(
    metric_name="technician_safety_protocol",
    prompt_template=(
        "Assess whether technician shutdown safety precautions appear in {response} "
        "for {prompt}. Return JSON score 1.0 or 0.0."
    ),
    display_name="Technician Safety Protocol",
    project_id="<your-gcp-project-id>",
    location="europe-west4",
)

# Manage registered metrics
all_metrics = list_registered_metrics(project_id="<your-gcp-project-id>", location="europe-west4")
fetched = get_registered_metric("has_valid_oem_pn", project_id="<your-gcp-project-id>", location="europe-west4")
client = vertexai.Client(project="<your-gcp-project-id>", location="europe-west4")
metric_ref = vtypes.Metric(
    name=fetched.metric_name, metric_resource_name=fetched.resource_name
)
delete_registered_metric("has_valid_oem_pn", project_id="<your-gcp-project-id>", location="europe-west4")
```

### 4.3 Diagnostic Loss Clustering (`generate_loss_clusters`)
`generate_loss_clusters()` executes Vertex AI GenAI loss analysis against `location="global"`:

```python
from agentops_sdk.evaluation import generate_loss_clusters

loss_report = generate_loss_clusters(
    cloud_eval_result,
    metric="multi_turn_tool_use_quality",
    max_clusters=5,
    project_id="<your-gcp-project-id>",
)
for cluster in loss_report.clusters:
    print(f"{cluster.cluster_id} ({cluster.l1_category}/{cluster.l2_category}): {cluster.percentage:.1f}%")
```

### 4.4 Cloud Agent Engine Evaluation (`evaluate_agent_engine`)
```python
from agentops_sdk.evaluation import UserSimulatorConfig, evaluate_agent_engine

run_result = evaluate_agent_engine(
    agent_engine_id="projects/<project-number>/locations/europe-west4/reasoningEngines/<engine-id>",
    dataset=[
        {"prompt": "What is the main hydraulic filter part number for Series-800?", "reference": "PART-H7042"}
    ],
    dest_gcs_uri="gs://<eval-bucket>/eval-runs",
    metrics=["multi_turn_tool_use_quality_v1", "multi_turn_task_success_v1"],
    user_simulator_config=UserSimulatorConfig(max_turns=5),
    project_id="<your-gcp-project-id>",
    location="europe-west4",
)
print(run_result.run_name, run_result.state, run_result.metrics_summary, run_result.console_url)
```

### 4.5 Prompt Experimentation (`ExperimentEngine` & `ExperimentConfig`)
```python
from agentops_sdk.evaluation import ExperimentConfig, ExperimentEngine

config = ExperimentConfig(
    name="equipment-diagnostic-prompts",
    prompt_template="Role: {{ persona }}. Context: {{ context }}. Query: {{ prompt }}",
    variants={
        "expert": {"persona": "OEM Master Technician"},
        "concise": {"persona": "Concise Service Assistant"},
    },
    dataset=[
        {
            "prompt": "Check DTC-HYDR-401 for Series-800.",
            "context": "DTC-HYDR-401 indicates low hydraulic flow; replace Part #PART-HYD-8821.",
            "reference": "Replace Part #PART-HYD-8821.",
            "response": "DTC-HYDR-401 indicates low hydraulic flow; replace Part #PART-HYD-8821.",
        }
    ],
    metrics=["keyword_coverage", "similarity"],
    evaluator="heuristic",
    tracking_backend="local",  # "none" | "local" | "vertex_ai"
    thresholds={"keyword_coverage": 0.80},
)

engine = ExperimentEngine(config=config)
results = engine.run()
print("Winner:", engine.get_winner(results, metric="keyword_coverage"))
```

### 4.6 Custom Domain Rubrics (`CustomRubricMetric`)
```python
from agentops_sdk.evaluation import (
    CustomRubricMetric,
    VertexAIEvalConfig,
    VertexEvalTaskAdapter,
)

warranty_rubric = CustomRubricMetric(
    name="warranty_compliance",
    criteria="Verify genuine OEM parts and certified fluids under warranty policy.",
    rubric={
        1: "Suggests uncertified parts or violates warranty policy.",
        3: "Partially compliant but omits warranty caveat.",
        5: "Strictly mandates genuine OEM parts and certified fluids.",
    },
    description="OEM warranty compliance metric.",
)

adapter = VertexEvalTaskAdapter(
    VertexAIEvalConfig(project_id="<your-gcp-project-id>", location="europe-west4", metrics=[warranty_rubric])
)
scores = adapter.evaluate([{"prompt": "P", "context": "C", "response": "R"}])
```

---

## 5. Verification & Test Suites

```bash
# Fast hermetic unit & contract suites (uses InMemoryEvaluator / HeuristicEvaluator)
pytest tests/unit/test_evaluation.py tests/unit/test_evaluation_genai.py tests/contract/test_evaluation_contract.py --eval-mode=in_memory -v

# Live GCP Vertex AI E2E suite
bash tests/e2e_vertex_evaluator/run_all_evaluator_tests.sh <your-gcp-project-id> europe-west4
```
