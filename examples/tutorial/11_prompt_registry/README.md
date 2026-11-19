# 11 · Prompt Registry: Versioned Prompts in Vertex AI Prompt Management (`v1.0.0`)

**What you learn:** how to externalize prompt templates with SemVer versioning, inject `VertexAIPromptAdapter` into `PromptRegistry`, inspect `PromptTemplate` metadata, and render variables safely via bounded Jinja2 `SandboxedEnvironment`.

```
PromptRegistry(backend="vertex_ai", adapter=VertexAIPromptAdapter(project, location), fallback_to_local=False)
  ├─ register_prompt(prompt_id, "1.0.0", template)  ──┐
  ├─ register_prompt(prompt_id, "1.1.0", template,  ──┼─► Vertex AI Prompt Management
  │                  system_instruction=...)        ──┘
  ├─ get_prompt(prompt_id, version="latest") ──► PromptTemplate (version="1.1.0", system_instruction, model_name)
  └─ render_prompt(prompt_id, variables, version="latest") ──► SandboxedEnvironment render ──► Gemini
```

## SDK calls used

| Call | What it does |
|---|---|
| `VertexAIPromptAdapter(project_id, location)` | Implements `PromptBackendPort` against Google Cloud Vertex AI Prompt Management |
| `PromptRegistry(backend="vertex_ai", adapter=..., fallback_to_local=False)` | Registry coordinator; `fallback_to_local=False` ensures missing cloud prompts fail loudly |
| `register_prompt(prompt_id, template, version, variables, description, system_instruction, model_name)` | Creates or updates a versioned prompt manifest |
| `list_versions(prompt_id)` / `get_prompt(prompt_id, version="latest")` | Lists SemVer versions and returns the full `PromptTemplate` object |
| `render_prompt(prompt_id, variables, version="latest")` | Loads and renders the template with `SandboxedEnvironment` |
| `LocalYAMLPromptAdapter`, `GCSPromptAdapter`, `InMemoryPromptAdapter` | Alternative backends for Git YAML, Cloud Storage, and offline tests |

Prometheus metrics emitted: `agentops.prompt.loads.total`, `agentops.prompt.renders.total`, and `agentops.prompt.fallbacks.total` (`0` when `fallback_to_local=False`).

## GCP resources used

| Resource | Why | Access needed |
|---|---|---|
| Vertex AI Prompt Management (`europe-west4`) | Stores versioned prompt templates | `roles/aiplatform.user` |
| Vertex AI, Gemini | Executes the rendered prompt | `roles/aiplatform.user` |

## Run

```bash
python examples/tutorial/11_prompt_registry/main.py
```

Expected: prints available Vertex AI prompt versions (`['1.0.0', '1.1.0']`), `latest` version metadata, and Gemini's two-sentence response. In Prometheus, `agentops_prompt_fallbacks_total` stays absent or `0`.
