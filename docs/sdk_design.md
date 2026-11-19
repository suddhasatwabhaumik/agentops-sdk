<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# Technical LLD: SDK Library & Architecture Specifications (`v1.0.0`)

**Version:** 1.0.0 (Hexagonal Ports & Adapters Architecture)  
**Status:** Active Production Standard  
**Branch:** `main`  

This document specifies the low-level technical design (LLD) for the AgentOps Instrumentation SDK (`agentops-sdk`). It details the 12-subpackage hexagonal layout, typed `ports/` contracts, PEP 562 lazy root exports, explicit dependency injection, configuration schemas, and OpenTelemetry pipeline.

> [!NOTE]
> See the [v1.0.0 Release Notes](../releases/v1.0.0_agentops_sdk_initial_release.md) for the full architecture overview and subsystem inventory.

---

## 1. Hexagonal 12-Subpackage Architecture & Layout

`agentops_sdk` enforces **Hexagonal Ports & Adapters** (`ADR-1`) across a pure-Python core (`domain/`, `ports/`, `config/`) and nine capability subpackages whose adapters inject cleanly without hidden mock fallbacks:

```text
sdk/src/agentops_sdk/
├── __init__.py             # PEP 562 lazy root facade (219 exported symbols in __all__: __version__, 12 subpackages, 206 lazy attributes; zero eager cloud imports)
├── pytest_plugin.py        # Auto-discovered agentops_eval plugin (--eval-mode, --fail-under-*)
│
├── domain/                 # Pure Python models, exceptions, ContextVar scope, bounded templating
│   ├── context.py          # ExecutionScope, bind_execution_scope, current_execution_scope
│   ├── exceptions.py       # AgentOpsError hierarchy (SecurityError, AuditLedgerError, QualityGateError, ...)
│   ├── lifecycle.py        # Service lifecycle helpers
│   ├── models.py           # TelemetryContext, AuditLogEntry, ConversationTurn, MemoryItem, RateLimitRule, ...
│   ├── predicates.py       # Shared validation predicates
│   ├── templating.py       # Bounded Jinja2 SandboxedEnvironment renderer
│   └── validation.py       # scrub_sensitive_text, validate_user_id
│
├── ports/                  # Pure typing.Protocol contracts (7 protocol modules)
│   ├── audit.py            # AuditSinkPort
│   ├── evaluation.py       # EvaluatorPort, ExperimentTrackerPort
│   ├── feature_store.py    # FeatureOnlineStorePort
│   ├── memory.py           # SessionMemoryPort, LongTermMemoryPort
│   ├── prompts.py          # PromptTemplate, PromptBackendPort
│   ├── security.py         # SafetyScannerPort, PrivacyScrubberPort, SecretStorePort
│   └── telemetry.py        # CaseTelemetryExporterPort
│
├── config/                 # Pydantic BaseSettings schemas (settings.py, _schemas.py)
│                           # GCPSettings, TelemetrySettings, SecuritySettings, DatabaseSettings,
│                           # RateLimitSettings, PromptSettings
│
├── telemetry/              # configure_telemetry, OtlpStdlibLogHandler, @trace_agent_flow,
│                           # @trace_agent_span, @trace_llm_call, log_llm_metrics,
│                           # UseCaseTracker, CloudLoggingTelemetryExporter, BigQueryTelemetryExporter,
│                           # BatchedGCSTelemetryExporter, CompositeTelemetryExporter, LocalFile*, InMemory*
├── audit/                  # AuditLedger, CloudLoggingAuditAdapter (default), BigQueryAuditAdapter,
│                           # LocalFileAuditAdapter, InMemoryAuditAdapter
├── memory/                 # CloudSQLSessionMemory, VertexMemoryBank, CompositeMemory,
│                           # InMemorySessionMemory, InMemoryLongTermMemory
├── evaluation/             # InMemoryEvaluator, HeuristicEvaluator, RagasEvaluator, VertexEvalTaskAdapter,
│                           # CompositeEvaluator, EvaluatorRegistry, ExperimentEngine, genai_evals,
│                           # metric_registry, simulator, code_validator
├── prompts/                # PromptRegistry, LocalYAMLPromptAdapter, GCSPromptAdapter,
│                           # VertexAIPromptAdapter, InMemoryPromptAdapter
├── security/               # SecurityGuard, CloudModelArmorScanner, LocalRuleSafetyScanner,
│                           # OutputDLPSanitizer, GoogleDLPScrubber, RegexPrivacyScrubber,
│                           # JWTAuthEngine, SecretManagerStore, InMemorySecretStore, @pass_user_identity, @inject_secret
├── sql/                    # SQLASTValidator, validate_sql_query, @validate_sql_query_decorator,
│                           # RowLevelPolicyEngine, apply_row_level_policy
├── resilience/             # @rate_limit, AsyncRateLimiter, SyncRateLimiter, @validate_tool_output
└── feature_store/          # FeatureStoreAdminClient, FeatureStoreOnlineClient, InMemoryFeatureOnlineStore
```

### Root `pyproject.toml` & Install Extras Matrix

[`pyproject.toml`](../pyproject.toml) sits at the repository root (`requires-python = ">=3.11,<3.15"`) with scoped optional extras so services install only what they use:

| Install Target | Packages Included | Primary Use Case |
| :--- | :--- | :--- |
| `pip install -e .` (Base) | `pydantic`, `pydantic-settings`, `opentelemetry-*`, `google-cloud-logging`, `google-cloud-bigquery`, `google-cloud-dlp`, `google-cloud-secret-manager`, `google-cloud-modelarmor`, `google-genai`, `sqlglot`, `pyjwt`, `jinja2` | Core instrumentation, safety, DLP, audit ledger, tracing, SQL AST/RLS, prompts |
| `.[cloudsql]` | `sqlalchemy`, `psycopg2-binary` | `CloudSQLSessionMemory` Postgres persistence |
| `.[eval]` | `google-cloud-aiplatform[evaluation]`, `pandas` | `VertexEvalTaskAdapter`, Vertex AI Agent Engine evals & `ExperimentEngine.get_summary_table()` |
| `.[ragas]` | `ragas`, `datasets` | `RagasEvaluator` metrics |
| `.[feature-store]` | `google-cloud-aiplatform` | `FeatureStoreAdminClient` & `FeatureStoreOnlineClient` |
| `.[mcp]` | `mcp[cli]`, `httpx` | FastMCP server runtime (`mcp_servers/src/server.py`) |
| `.[all]` | `cloudsql`, `eval`, `ragas`, `mcp` | Full local development, examples, & tutorials |
| `.[dev]` | `pytest`, `pytest-asyncio`, `pytest-cov`, `httpx`, `fastapi`, `python-dotenv`, `ruff`, `mypy`, `types-PyYAML` | Unit/contract tests, Ruff linting, strict mypy |

---

## 2. Core Port Interfaces (`ports/`)

Every adapter boundary is defined as a `@runtime_checkable` `typing.Protocol`, enabling structural typing across GCP services and explicit `InMemory*` / `Local*` test doubles:

### A. Audit Sink (`ports/audit.py`)
```python
from typing import Protocol, runtime_checkable

from agentops_sdk.domain import models


@runtime_checkable
class AuditSinkPort(Protocol):
    def write_entry(self, entry: models.AuditLogEntry) -> bool: ...
```

### B. Safety & Privacy (`ports/security.py`)
```python
@runtime_checkable
class SafetyScannerPort(Protocol):
    def scan_input(self, text: str) -> models.SafetyScanResult: ...
    def scan_output(self, text: str) -> models.SafetyScanResult: ...


@runtime_checkable
class PrivacyScrubberPort(Protocol):
    def scrub(self, text: str) -> tuple[str, bool]: ...
    async def ascrub(self, text: str) -> tuple[str, bool]: ...
```

### C. Session & Long-Term Memory (`ports/memory.py`)
```python
@runtime_checkable
class SessionMemoryPort(Protocol):
    def add_message(self, session_id: str, role: str, content: str, metadata=None) -> models.ConversationTurn: ...
    def get_messages(self, session_id: str, *, limit: int = 50, offset: int = 0, after_id=None, before_id=None) -> list[models.ConversationTurn]: ...
    def clear_session(self, session_id: str) -> int: ...


@runtime_checkable
class LongTermMemoryPort(Protocol):
    def create_memory(self, fact: str, *, user_id: str, memory_type: str = "fact", metadata=None, ttl_seconds=None) -> models.MemoryItem: ...
    def retrieve_memories(self, *, user_id: str, query: str | None = None, limit: int = 5) -> list[models.MemoryItem]: ...
    def purge_memories(self, *, user_id: str) -> int: ...
```

### D. Evaluation, Prompts, Telemetry & Feature Store
*   `ports/evaluation.py`: `EvaluatorPort` (`evaluate(dataset, *, metrics=None)`) and `ExperimentTrackerPort` (`log_run(...)`).
*   `ports/prompts.py`: `PromptBackendPort` (`get_prompt`, `register_prompt`, `list_prompts`, `list_versions`, `delete_prompt`).
*   `ports/telemetry.py`: `CaseTelemetryExporterPort` (`export(record: UseCaseTelemetry) -> bool`).
*   `ports/feature_store.py`: `FeatureOnlineStorePort` (`read_online_features(...)`, `aread_online_features(...)`).

---

## 3. Explicit Dependency Injection & PEP 562 Lazy Facade

All SDK components follow **explicit adapter injection** (`ADR-2`):

1.  **Zero hidden mock fallbacks**: Production adapters (`CloudModelArmorScanner`, `GoogleDLPScrubber`, `BigQueryAuditAdapter`, `CloudSQLSessionMemory`, `VertexMemoryBank`, `VertexEvalTaskAdapter`) raise typed `AgentOpsError` subclasses on misconfiguration or transport errors. Unit tests inject `LocalRuleSafetyScanner`, `RegexPrivacyScrubber`, `InMemoryAuditAdapter`, `InMemorySessionMemory`, `InMemoryLongTermMemory`, `InMemoryEvaluator`, or `InMemoryFeatureOnlineStore` explicitly.
2.  **Single-stream Cloud Logging defaults**: `AuditLedger()` and `UseCaseTracker()` default to `CloudLoggingAuditAdapter` (`agentops-audit-ledger`) and `CloudLoggingTelemetryExporter` (`agentops-use-case-telemetry`) respectively, letting Cloud Log Routers sink records asynchronously without hot-path BigQuery latency unless `BigQueryAuditAdapter` / `BigQueryTelemetryExporter` is passed explicitly.
3.  **Lazy imports**: Importing `from agentops_sdk import AuditLedger, SecurityGuard` resolves lazily via PEP 562 (`_EXPORT_MAP`), avoiding eager imports of `vertexai` or database drivers at startup.

```python
from agentops_sdk.audit import AuditLedger, BigQueryAuditAdapter
from agentops_sdk.domain.context import bind_execution_scope
from agentops_sdk.security import CloudModelArmorScanner, SecurityGuard
from agentops_sdk.sql import apply_row_level_policy, validate_sql_query

guard = SecurityGuard(scanner=CloudModelArmorScanner(), fail_closed=True)
ledger = AuditLedger(sink=BigQueryAuditAdapter(dataset_id="agentops_governance"))

with bind_execution_scope(session_id="sess-1", user_id="dealer-4711"):
    check = guard.check_input("List hydraulic stock for my region")
    sql_res = validate_sql_query(
        "SELECT part_id, region FROM analytics.dealer_stock",
        dialect="bigquery",
        allowed_tables={"analytics.dealer_stock"},
    )
    scoped_sql = apply_row_level_policy(
        sql_res.sanitized_sql,
        {"sub": "dealer-4711", "region": "Germany"},
        dialect="bigquery",
        policy_columns={"region": "region"},
    )
```

---

## 4. Unified OpenTelemetry Observability (`telemetry/provider.py`)

`configure_telemetry()` unifies trace, metric, and log initialization across five explicit modes (`TelemetryMode`: `AUTO`, `GCP`, `OTLP`, `HYBRID`, `OFF`):

```python
from agentops_sdk.telemetry import configure_telemetry

handles = configure_telemetry(
    mode="otlp",  # auto | gcp | otlp | hybrid | off
    service_name="agentops-service",
    collector_url="http://127.0.0.1:4317",
)
# ... run traced workflows ...
handles.shutdown()
```

*   **`OtlpStdlibLogHandler`**: Automatically attached in `otlp` / `hybrid` modes (and resolved `auto` with `OTEL_COLLECTOR_URL`), forwarding `agentops_sdk*` and `agentops.*` logs with flattened `extra["json_fields"]` attributes over OTLP gRPC.
*   **FinOps Cloud Logging Isolation**: `@trace_llm_call` only ships FinOps records to Cloud Logging (`agentops-finops-telemetry`) when the active telemetry mode is `gcp` or `hybrid`.
*   **Context Propagation**: `@trace_agent_flow`, `@trace_agent_span`, and `@trace_llm_call` automatically read `current_execution_scope()` (`session_id`, `user_id`, `telemetry_context`) via Python `contextvars.ContextVar` across both sync and `asyncio` tasks.
