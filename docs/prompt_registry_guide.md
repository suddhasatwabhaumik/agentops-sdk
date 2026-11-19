<!--
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
-->

# Prompt Registry Architecture & Operator Guide (`v1.0.0`)

**Subpackage:** `agentops_sdk.prompts`  
**Port Contract:** `agentops_sdk.ports.prompts.PromptBackendPort`  
**Primary Class:** `PromptRegistry`

---

## 1. Executive Summary

Hardcoding system instructions inside agent code causes version drift, couples prompt edits to code deploys, and prevents systematic A/B evaluation. `agentops_sdk.prompts` manages SemVer prompt templates behind `PromptBackendPort` across four concrete backends:

1. **Local YAML Backend (`LocalYAMLPromptAdapter`, default `backend="local"` / `"git"`)**: Loads and persists SemVer-versioned YAML files inside a repository folder with path-traversal protection and `RLock` thread safety.
2. **Google Cloud Storage Backend (`GCSPromptAdapter`, `backend="gcs"`)**: Stores SemVer YAML manifests in versioned GCS buckets (`gcs_bucket`, `gcs_prefix`).
3. **Vertex AI Prompt Management (`VertexAIPromptAdapter`, `backend="vertex_ai"`)**: Integrates directly with Vertex AI Prompt Management in GCP (`project_id`, `location`).
4. **In-Memory Backend (`InMemoryPromptAdapter`, `backend="in_memory"`)**: Clean test double for deterministic offline unit tests.

> [!IMPORTANT]
> **Fail-Closed Default (`v1.0.0`)**: `PromptRegistry` defaults `fallback_to_local=False` (`PROMPT_REGISTRY_FALLBACK_TO_LOCAL=false`). Opt in explicitly via `fallback_to_local=True` or `fallback_adapter=...` when local failover on primary backend outages is intended; failover emits `agentops.prompt.fallbacks.total`.

---

## 2. Hexagonal Architecture & Contracts

```text
                         ┌────────────────────────────────────────────────────────────┐
                         │                       PromptRegistry                       │
                         │                                                            │
                         │  • get_prompt(prompt_id, version="latest") -> PromptTemplate
                         │  • render_prompt(prompt_id, variables, version) -> str     │
                         │  • format_prompt(template_or_id, variables) -> str         │
                         │  • register_prompt(prompt=None, *, prompt_id, template...) │
                         │  • list_prompts() / list_versions() / delete_prompt()      │
                         └──────────────────────────────┬─────────────────────────────┘
                                                        │
                                          PromptBackendPort (Protocol)
                                                        ▼
            ┌──────────────────┬────────────────────────┼────────────────────────┬──────────────────┐
            ▼                  ▼                        ▼                        ▼                  ▼
  LocalYAMLPromptAdapter   GCSPromptAdapter   VertexAIPromptAdapter        InMemoryPromptAdapter   fallback_adapter
  (YAML files on disk)     (GCS object YAML)  (Vertex AI Prompt Management) (Offline unit tests)    (Optional failover)
```

### 2.1 `PromptTemplate` Schema (`agentops_sdk.ports.prompts`)
```python
class PromptTemplate(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True)

    prompt_id: str
    version: str = "1.0.0"
    template: str
    description: str = ""
    model_name: str = "gemini-2.5-flash"
    system_instruction: str | None = None
    variables: tuple[str, ...] = ()
    generation_config: dict[str, Any] = pydantic.Field(default_factory=dict)
    metadata: dict[str, Any] = pydantic.Field(default_factory=dict)
```

> [!NOTE]
> `get_prompt()` returns a **`PromptTemplate` object** (containing `.template`, `.version`, `.system_instruction`, `.model_name`, `.metadata`), **not** a bare string. Call `render_prompt()` or inspect `tmpl.template` for interpolated strings.

---

## 3. Usage Patterns

### 3.1 Local YAML Workflow (`prompts/*.yaml`)
Standard YAML prompt definition:

```yaml
prompt_id: sql_agent_prompt
version: 1.0.0
description: "Production prompt template for NL2SQL analytical queries."
model_name: gemini-2.5-flash
template: |
  You are an expert SQL analytics agent generating queries for {{ dialect }}.
  Database Schema Context:
  {{ schema_context }}

  User Query:
  {{ user_query }}
variables:
  - dialect
  - schema_context
  - user_query
metadata:
  domain: database_analytics
  author: AgentOps
```

Python usage:

```python
from agentops_sdk.prompts import PromptRegistry

registry = PromptRegistry(backend="local", local_dir="prompts")

tmpl = registry.get_prompt("sql_agent_prompt", version="latest")
print(f"Loaded version {tmpl.version}")

rendered_sql = registry.render_prompt(
    "sql_agent_prompt",
    {
        "dialect": "BigQuery",
        "schema_context": "TABLE customers (id INT64, name STRING, region STRING)",
        "user_query": "List top customers in EMEA",
    },
)
```

### 3.2 Vertex AI Prompt Management with Explicit Adapter & Optional Fallback
```python
from agentops_sdk.prompts import (
    LocalYAMLPromptAdapter,
    PromptRegistry,
    VertexAIPromptAdapter,
)

# Strict mode (fail closed on missing Vertex AI prompts):
strict_registry = PromptRegistry(
    backend="vertex_ai",
    adapter=VertexAIPromptAdapter(project_id="<your-gcp-project-id>", location="europe-west4"),
    fallback_to_local=False,
)

# Resilient mode (explicit fallback adapter recorded via agentops.prompt.fallbacks.total):
resilient_registry = PromptRegistry(
    backend="vertex_ai",
    adapter=VertexAIPromptAdapter(project_id="<your-gcp-project-id>", location="europe-west4"),
    fallback_adapter=LocalYAMLPromptAdapter(local_dir="prompts"),
)
```

### 3.3 True SemVer Resolution
`PromptRegistry` parses numeric SemVer components (`1.0.0 < 1.2.0 < 1.10.0 < 2.0.0`):

```python
registry.register_prompt(prompt_id="analyst", template="v1.2", version="1.2.0")
registry.register_prompt(prompt_id="analyst", template="v1.10", version="1.10.0")
registry.register_prompt(prompt_id="analyst", template="v2.0", version="2.0.0")

assert registry.get_prompt("analyst", version="latest").template == "v2.0"
assert registry.get_prompt("analyst", version="1.10.0").template == "v1.10"
```

---

## 4. Security Hardening & Concurrency

- **Path Traversal Defense**: `prompt_id` values undergo strict identifier validation blocking `..`, `/`, `\`, and null/control characters; `LocalYAMLPromptAdapter` resolves file targets strictly inside `local_dir`.
- **Bounded Sandboxed Jinja2 Rendering**: `render_prompt()` and `format_prompt()` render exclusively via `agentops_sdk.domain.templating.render_sandboxed_template()` using Jinja2 `SandboxedEnvironment` (`DEFAULT_MAX_TEMPLATE_CHARS = 256_000`), blocking SSTI attribute traversal and unbounded loops.
- **Thread Safety**: Adapters synchronize shared registries with `threading.RLock`.

---

## 5. OpenTelemetry Metrics

| Metric Name | Instrument | Description | Attributes |
|---|---|---|---|
| `agentops.prompt.loads.total` | Counter | Prompt retrieval attempts | `backend`, `status` |
| `agentops.prompt.renders.total` | Counter | Template renders | `status`, `engine` (`"jinja2_sandboxed"`) |
| `agentops.prompt.fallbacks.total` | Counter | Primary backend failover events | `from_backend`, `to_backend` |

---

## 6. Configuration Reference (`PromptSettings`)

| Environment Variable | Type | Default | Description |
|---|---|---|---|
| `PROMPT_REGISTRY_BACKEND` | `str` | `"local"` | Default backend: `"local"`, `"git"`, `"vertex_ai"`, or `"gcs"` |
| `PROMPT_REGISTRY_LOCAL_DIR` | `str` | `"prompts"` | Directory containing local prompt YAML files |
| `PROMPT_REGISTRY_GCS_BUCKET` | `str \| None` | `None` | Target GCS bucket when `backend="gcs"` |
| `PROMPT_REGISTRY_GCS_PREFIX` | `str` | `"prompts"` | Object prefix within `PROMPT_REGISTRY_GCS_BUCKET` |
| `PROMPT_REGISTRY_PROJECT_ID` | `str \| None` | `GCPSettings` | GCP Project ID (`GOOGLE_CLOUD_PROJECT` / `GCP_PROJECT` fallback) |
| `PROMPT_REGISTRY_LOCATION` | `str \| None` | `GCPSettings` | GCP region |
| `PROMPT_REGISTRY_FALLBACK_TO_LOCAL` | `bool` | `False` | Enable local YAML fallback (legacy alias `PROMPT_REGISTRY_FALLBACK_TO_GIT`) |

---

## 7. Verification & E2E Tests

```bash
# Hermetic unit & contract prompt suites
pytest tests/unit/test_prompts.py tests/contract/test_prompts_contract.py --eval-mode=in_memory -v

# Live GCP Vertex AI prompt E2E suite
./tests/e2e_prompt_registry/run_all_prompt_registry_tests.sh <your-gcp-project-id> europe-west4
```
