# Memory, Prompts & Evaluation (`v1.0.0`)

## 1. Session & Long-Term Memory

- **`CloudSQLSessionMemory`**: SQLAlchemy Core PostgreSQL adapter with keyset (`after_id`, `before_id`) and offset pagination (`DEFAULT_PAGE_SIZE=50`, `MAX_PAGE_SIZE=200`).
- **`VertexMemoryBank`**: Canonical `scope={"user_id": user_id}` with optional `memory_type` filtering on `retrieve_memories()` / `aretrieve_memories()` using bounded single-RPC over-fetch `min(max(limit * 4, 20), 100)`.
- **`CompositeMemory`**: Unified `get_context()` / `aget_context()` and `consolidate_session()`.

## 2. Prompt Registry

- **`PromptRegistry`**: Semver-governed templates across `VertexAIPromptAdapter`, `GCSPromptAdapter`, `LocalYAMLPromptAdapter`, and `InMemoryPromptAdapter`.
- Bounded Jinja2 `SandboxedEnvironment` rendering with opt-in `fallback_to_local=False` default.

## 3. Vertex AI Evaluation & CI/CD Quality Gates

- **`VertexEvalTaskAdapter`**:
  - Default judge/simulator model: **`gemini-3.8-flash`** (overridable via `AGENTOPS_EVAL_AUTORATER_MODEL` > `AGENTOPS_EVAL_JUDGE_MODEL`).
  - Expands bare judge names to `projects/{proj}/locations/{judge_location}/publishers/google/models/{judge}` (`judge_location="global"` default) and routes `AutoraterConfig` through `vertexai.preview.evaluation.EvalTask` (logging a warning if unsupported).
  - Suppresses synthetic `/experiments/default/` URLs when `experiment` is unset.
- **Quality Gates**: `evaluate_agent_response()` and `assert_quality_gate()` populate `EvaluationResult.explanations`, `raw_scores`, and `console_url` into `QualityGateSessionReporter` Markdown summaries.

See [docs/session_and_long_term_memory_guide.md](../docs/session_and_long_term_memory_guide.md) and [docs/vertex_ai_evaluator_guide.md](../docs/vertex_ai_evaluator_guide.md).
