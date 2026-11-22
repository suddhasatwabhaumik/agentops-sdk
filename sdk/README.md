```
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
```

# AgentOps Instrumentation SDK (`agentops-sdk` v1.0.0)

Enterprise Python SDK providing OpenTelemetry tracing, FinOps attribution, fail-closed Cloud Model Armor guardrails, regional Cloud DLP PII scrubbing, SQL AST and Row-Level Security governance, Cloud SQL / Vertex Memory Bank persistence, Feature Store synchronization, semver Prompt Registry governance, async/sync rate limiting, CI/CD evaluation quality gates, and single-stream Cloud Logging / BigQuery observability.

> [!NOTE]
> See the [v1.0.0 Release Notes](../releases/v1.0.0_agentops_sdk_initial_release.md) for the full architecture overview and subsystem inventory.

---

## 1. Installation & Extras

[`pyproject.toml`](../pyproject.toml) lives at the repository root (`requires-python = ">=3.11,<3.15"`). Always install directly from the repository root.

```bash
# Editable developer install (recommended inside virtualenv):
pip install -e ".[dev,all]"

# Production editable or local install with all optional extras:
pip install -e ".[all]"

# Scoped extras (pick only what your agent runtime uses):
pip install -e ".[cloudsql,eval]"
```

| Extra | Optional Dependencies Included |
| :--- | :--- |
| `(base)` | Google Cloud Logging, BigQuery, DLP, Model Armor, Secret Manager, `google-genai`, `opentelemetry-*`, `sqlglot>=30.0.0`, `pydantic-settings`, `jinja2`, `pyjwt>=2.10.0` |
| `cloudsql` | `sqlalchemy`, `psycopg2-binary` |
| `eval` | `google-cloud-aiplatform[evaluation]`, `pandas` |
| `ragas` | `ragas`, `datasets` |
| `feature-store` | `google-cloud-aiplatform` |
| `mcp` | `mcp[cli]`, `httpx` |
| `all` | `cloudsql`, `eval`, `ragas`, `mcp` |
| `dev` | `pytest`, `pytest-asyncio`, `pytest-cov`, `httpx`, `fastapi`, `python-dotenv`, `ruff`, `mypy`, `types-PyYAML` |

### Installing from GitHub Releases or Git
```bash
# 1. Pre-built wheel via GitHub CLI (Private Enterprise SSO):
gh release download v1.0.0 \
  --repo <ORG>/<REPO> \
  --pattern "*.whl" \
  --dir /tmp --clobber && \
pip install "/tmp/agentops_sdk-1.0.0-py3-none-any.whl[all]"

# 2. Direct pip install over SSH:
pip install "agentops-sdk[all] @ git+ssh://git@github.com/<ORG>/<REPO>.git@v1.0.0"

# 3. Direct pip install over HTTPS with an SSO-authorized GITHUB_PAT:
pip install "agentops-sdk[all] @ git+https://<GITHUB_PAT>@github.com/<ORG>/<REPO>.git@v1.0.0"
```

---

## 2. Hexagonal 12-Subpackage Architecture

`agentops_sdk` uses a strict Hexagonal (Ports & Adapters) layout. Heavy GCP adapters are loaded lazily when importing from the top-level `agentops_sdk` PEP 562 facade, or imported explicitly from their subpackage:

| Subpackage | Responsibility & Key Symbols |
| :--- | :--- |
| `agentops_sdk.domain` | Pure DTOs (`TelemetryContext`, `AuditLogEntry`, `ConversationTurn`, `MemoryItem`, `RateLimitRule`, `RateLimitAlgorithm`), exceptions, `bind_execution_scope`, `SandboxedEnvironment` templating, PII scrubbing |
| `agentops_sdk.ports` | Typed `typing.Protocol` interfaces (`AuditSinkPort`, `EvaluatorPort`, `ExperimentTrackerPort`, `SessionMemoryPort`, `LongTermMemoryPort`, `PromptBackendPort`, `SafetyScannerPort`, `PrivacyScrubberPort`, `SecretStorePort`, `FeatureOnlineStorePort`, `CaseTelemetryExporterPort`) |
| `agentops_sdk.config` | Pydantic settings (`GCPSettings`, `TelemetrySettings`, `SecuritySettings`, `DatabaseSettings`, `RateLimitSettings`, `PromptSettings`) |
| `agentops_sdk.telemetry` | `configure_telemetry`, `OtlpStdlibLogHandler`, `@trace_agent_flow`, `@trace_agent_span`, `@trace_llm_call`, `log_llm_metrics`, `UseCaseTracker`, `@trace_use_case`, `CloudLoggingTelemetryExporter`, `BigQueryTelemetryExporter`, `BatchedGCSTelemetryExporter`, `CompositeTelemetryExporter`, `LocalFileTelemetryExporter`, `InMemoryTelemetryExporter` |
| `agentops_sdk.audit` | `AuditLedger`, `CloudLoggingAuditAdapter` (default), `BigQueryAuditAdapter`, `LocalFileAuditAdapter`, `InMemoryAuditAdapter` |
| `agentops_sdk.memory` | `CloudSQLSessionMemory`, `VertexMemoryBank`, `CompositeMemory`, `InMemorySessionMemory`, `InMemoryLongTermMemory` |
| `agentops_sdk.evaluation` | `EvaluationResult`, `InMemoryEvaluator`, `HeuristicEvaluator`, `RagasEvaluator`, `VertexEvalTaskAdapter`, `CompositeEvaluator`, `EvaluatorRegistry`, `evaluate_agent_response`, `assert_quality_gate`, `ExperimentEngine`, `genai_evals`, `metric_registry`, `simulator` |
| `agentops_sdk.prompts` | `PromptRegistry`, `PromptTemplate`, `LocalYAMLPromptAdapter`, `GCSPromptAdapter`, `VertexAIPromptAdapter`, `InMemoryPromptAdapter` |
| `agentops_sdk.security` | `SecurityGuard`, `CloudModelArmorScanner`, `LocalRuleSafetyScanner`, `OutputDLPSanitizer`, `GoogleDLPScrubber`, `RegexPrivacyScrubber`, `JWTAuthEngine`, `SecretManagerStore`, `InMemorySecretStore`, `@pass_user_identity`, `@inject_secret` |
| `agentops_sdk.sql` | `SQLASTValidator`, `validate_sql_query`, `@validate_sql_query_decorator`, `RowLevelPolicyEngine`, `apply_row_level_policy` |
| `agentops_sdk.resilience` | `@rate_limit`, `AsyncRateLimiter`, `SyncRateLimiter`, `@validate_tool_output` |
| `agentops_sdk.feature_store` | `FeatureStoreAdminClient`, `FeatureStoreOnlineClient`, `InMemoryFeatureOnlineStore` |
| `agentops_sdk.pytest_plugin` | Auto-discovered `agentops_eval` pytest plugin (`--eval-mode=in_memory | heuristic | vertex`, `--fail-under-*`) |

---

## 3. Usage Examples

### 3.1 Tracing, FinOps Correlation & OTLP Export
`configure_telemetry()` initializes OpenTelemetry trace, metric, and log (`OtlpStdlibLogHandler`) pipelines. FinOps spans record `business.kpi_id`, `business.pl_impact_line`, `llm.model_name`, `llm.usage.prompt_tokens`, `llm.usage.completion_tokens`, and `gen_ai.*` conventions.

```python
from agentops_sdk.domain.context import bind_execution_scope
from agentops_sdk.domain.models import TelemetryContext
from agentops_sdk.telemetry import configure_telemetry
from agentops_sdk.telemetry import trace_agent_flow
from agentops_sdk.telemetry import trace_llm_call

handles = configure_telemetry(mode="otlp", service_name="supply-chain-agent")

FINOPS_CTX = TelemetryContext(
    business_kpi="supply-chain-lead-time-reduction",
    pl_impact_line="working-capital-reduction",
    project_name="agentops-demo",
    environment="dev",
)


@trace_llm_call(context=FINOPS_CTX)
def call_gemini(prompt: str) -> dict[str, object]:
    return {
        "model_name": "gemini-2.5-flash",
        "input_tokens": 120,
        "output_tokens": 48,
        "response": f"Verified response for: {prompt}",
    }


@trace_agent_flow(context=FINOPS_CTX, agent_role="inventory_planner")
def run_workflow(user_query: str) -> str:
    result = call_gemini(user_query)
    return str(result["response"])


with bind_execution_scope(session_id="sess-001", user_id="UNIT-001"):
    output = run_workflow("Check hydraulic pump P-100 availability")
handles.shutdown()
```

### 3.2 Fail-Closed Model Armor Safety (`SecurityGuard`)
`SecurityGuard` defaults to `fail_closed=True`. Inject `CloudModelArmorScanner` for production GCP enforcement or `LocalRuleSafetyScanner` explicitly for offline tests.

```python
from agentops_sdk.domain.models import SecurityVerdict
from agentops_sdk.security import LocalRuleSafetyScanner
from agentops_sdk.security import SecurityGuard

guard = SecurityGuard(scanner=LocalRuleSafetyScanner(), fail_closed=True)

inbound = guard.check_input("Summarize stock levels for tenant-acme")
if not inbound.allowed or inbound.verdict == SecurityVerdict.BLOCK:
    raise RuntimeError(f"Blocked prompt: {inbound.violation_category}")

outbound = guard.check_output("Contact buyer@example.com for invoice status.")
safe_reply = outbound.sanitized_text
```

### 3.3 Output PII Scrubbing (`OutputDLPSanitizer`)
Inject `GoogleDLPScrubber(project_id=..., location=...)` for regional Cloud DLP endpoints (`dlp.{location}.rep.googleapis.com`), or `RegexPrivacyScrubber()` explicitly offline:

```python
from agentops_sdk.security import OutputDLPSanitizer
from agentops_sdk.security import RegexPrivacyScrubber

sanitizer = OutputDLPSanitizer(scrubber=RegexPrivacyScrubber())
redacted = sanitizer.scrub("Reach John Doe at john.doe@example.com.")
```

### 3.4 SQL AST Validation & Row-Level Security
`SQLASTValidator` (`sqlglot>=30.0.0`) enforces read-only `SELECT` statements and table allowlists (`dataset.*`). `RowLevelPolicyEngine` performs scope-aware AST rewriting (`LEFT JOIN ... ON`, dialect-normalized CTEs, sole-`FROM` `ML.PREDICT`, `TableFromRows`/`PIVOT`/`UNPIVOT`/column-list alias rejection, post-rewrite verification) with role-gated admin bypasses (`role in {"admin", "superadmin"}`).

```python
from agentops_sdk.sql import apply_row_level_policy
from agentops_sdk.sql import validate_sql_query

raw_sql = "SELECT part_id, region FROM enterprise_parts.inventory_stock WHERE qty > 0"
check = validate_sql_query(
    raw_sql,
    dialect="bigquery",
    allowed_tables={"enterprise_parts.inventory_stock"},
)
scoped_sql = apply_row_level_policy(
    check.sanitized_sql,
    claims={"sub": "u-1", "role": "analyst", "country": "DE"},
    dialect="bigquery",
    policy_columns={"region": "country"},
)
```

### 3.5 Tool Output Validation (`@validate_tool_output`)
```python
from pydantic import BaseModel

from agentops_sdk.resilience import validate_tool_output


class StockRecord(BaseModel):
    part_id: str
    stock_level: int


@validate_tool_output(schema_model=StockRecord, max_retries=3)
def fetch_stock_record(part_id: str) -> dict[str, object]:
    return {"part_id": part_id, "stock_level": 24}
```

### 3.6 Rate Limiting & Concurrency Control (`@rate_limit`)
```python
from agentops_sdk.domain.models import RateLimitAlgorithm
from agentops_sdk.resilience import rate_limit


@rate_limit(
    rate=60,
    period_seconds=60.0,
    burst=10,
    max_concurrency=5,
    algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
)
async def call_supplier_api(supplier_id: str) -> dict[str, str]:
    return {"supplier_id": supplier_id, "status": "ok"}
```

### 3.7 Conversational Session & Long-Term Memory
`CloudSQLSessionMemory` stores turns in Postgres/Cloud SQL (use `InMemorySessionMemory` offline), while `VertexMemoryBank` persists long-term facts using canonical `scope={"user_id": user_id}`, native `search_query` retrieval, and optional `memory_type` filtering via bounded single-RPC over-fetch (`min(max(limit * 4, 20), 100)`):

```python
from agentops_sdk.memory import CompositeMemory
from agentops_sdk.memory import InMemoryLongTermMemory
from agentops_sdk.memory import InMemorySessionMemory

session_mem = InMemorySessionMemory()
long_term_mem = InMemoryLongTermMemory()
memory = CompositeMemory(session_mem, long_term_mem)

memory.add_message("sess-1", "user", "Prefer metric torque specs in Nm.")
memory.create_memory(
    "Tenant tenant-acme prefers metric torque values.",
    user_id="UNIT-001",
    memory_type="preference",
)
ctx = memory.get_context("sess-1", "UNIT-001", query="torque", strict=True)
```

### 3.8 Semver Prompt Registry
```python
from agentops_sdk.prompts import InMemoryPromptAdapter
from agentops_sdk.prompts import PromptRegistry

registry = PromptRegistry(adapter=InMemoryPromptAdapter())
registry.register_prompt(
    prompt_id="parts_advisor",
    version="1.0.0",
    template="Summarize availability for part {{ part_id }} in {{ region }}.",
)
rendered = registry.render_prompt(
    "parts_advisor",
    {"part_id": "P-100", "region": "EMEA"},
)
```

### 3.9 Evaluation Quality Gates & Prompt Experiments
`VertexEvalTaskAdapter` and `simulator` default to `gemini-3.8-flash` (`AGENTOPS_EVAL_AUTORATER_MODEL`, full publisher URI + `judge_location="global"`), route `AutoraterConfig` via preview when needed (warning when unsupported), and populate `EvaluationResult.explanations`, `raw_scores`, and `console_url` automatically in quality gates.
```python
from agentops_sdk.evaluation import ExperimentConfig
from agentops_sdk.evaluation import ExperimentEngine
from agentops_sdk.evaluation import evaluate_agent_response

dataset = [
    {
        "prompt": "What is the stock level of P-100?",
        "context": "Inventory table: P-100 stock level is 12 units.",
        "response": "Part P-100 has 12 units available.",
    }
]
result = evaluate_agent_response(
    dataset,
    thresholds={"groundedness": 0.85, "question_answering_relevance": 0.80},
    evaluator="in_memory",
)

exp = ExperimentEngine(
    ExperimentConfig(
        name="parts_prompt_ab",
        prompt_template="Answer ({{ style }}) using {{ context }}: {{ prompt }}",
        variants={"v1": {"style": "Concise"}},
        dataset=dataset,
        evaluator="in_memory",
    )
)
scores_by_variant = exp.run()
```

### 3.10 Audit Ledger & Per-Case Telemetry (ADR-2)
Both `AuditLedger()` and `UseCaseTracker()` default to single-stream Cloud Logging (`agentops-audit-ledger` and `agentops-use-case-telemetry`). Inject BigQuery, batched GCS, composite, hardened local-file, or in-memory adapters explicitly:

```python
from agentops_sdk.audit import AuditLedger
from agentops_sdk.audit import InMemoryAuditAdapter
from agentops_sdk.domain.models import AuditLogEntry
from agentops_sdk.telemetry import InMemoryTelemetryExporter
from agentops_sdk.telemetry import UseCaseTracker

ledger = AuditLedger(sink=InMemoryAuditAdapter())
ledger.log_entry_sync(
    AuditLogEntry(
        trace_id="trace-abc",
        session_id="sess-1",
        user_id="UNIT-001",
        agent_name="PartsAdvisor",
        input_query="SELECT part_id FROM enterprise_parts.inventory_stock",
        execution_status="SUCCESS",
        rows_returned=5,
        latency_ms=42.1,
    )
)

exporter = InMemoryTelemetryExporter()
with UseCaseTracker("inventory_stock_lookup", exporter=exporter) as tracker:
    tracker.start_stage("sql_query")
    tracker.add_transactions(5)
    tracker.end_stage("sql_query")
```

### 3.11 Vertex AI Feature Online Store Synchronization
```python
from agentops_sdk.feature_store import InMemoryFeatureOnlineStore

store = InMemoryFeatureOnlineStore()
store.seed_features(
    "enterprise_online_store",
    "enterprise_features_view",
    {"UNIT-001": {"part_stock_ratio": 0.92, "lead_time_days": 4}},
)
features = store.read_online_features(
    "enterprise_online_store",
    "enterprise_features_view",
    entity_ids=["UNIT-001"],
    features=["part_stock_ratio", "lead_time_days"],
)
```
In GCP environments, swap `InMemoryFeatureOnlineStore` for `FeatureStoreAdminClient` (`ensure_online_store`, `ensure_feature_view`, `trigger_sync`) and `FeatureStoreOnlineClient` (`read_online_features`).

---

## 4. Environment Variable Reference

| Category | Variable (Primary / Aliases) | Default | Description |
| :--- | :--- | :--- | :--- |
| **GCP** | `GOOGLE_CLOUD_PROJECT`, `GCP_PROJECT`, `AGENTOPS_PROJECT_ID` | `None` | Target GCP project ID |
| **GCP** | `GOOGLE_CLOUD_LOCATION`, `GOOGLE_CLOUD_REGION`, `GCP_REGION`, `AGENTOPS_LOCATION` | `"europe-west4"` | Default GCP region |
| **Telemetry** | `AGENTOPS_TELEMETRY_MODE`, `OTEL_MODE` | `"auto"` | Telemetry mode: `auto \| gcp \| otlp \| hybrid \| off` |
| **Telemetry** | `OTEL_COLLECTOR_URL`, `OTEL_EXPORTER_OTLP_ENDPOINT` | `None` | gRPC OTLP collector endpoint |
| **Telemetry** | `OTEL_SERVICE_NAME`, `AGENTOPS_FINOPS_LOG_NAME` | `"agentops-service"`, `"agentops-finops-telemetry"` | OTel service name & FinOps Cloud Log name |
| **Security** | `MODEL_ARMOR_PROJECT_ID`, `SECURITY_PROJECT_ID` | Inherits `GCPSettings` | Project hosting Model Armor templates |
| **Security** | `MODEL_ARMOR_LOCATION`, `MODEL_ARMOR_REGION`, `SECURITY_LOCATION`, `SECURITY_REGION` | `"europe-west4"` | Model Armor endpoint region |
| **Security** | `MODEL_ARMOR_INPUT_TEMPLATE`, `MODEL_ARMOR_TEMPLATE_ID`, `SECURITY_TEMPLATE_ID` | `"agentops-input-guard"` | Input sanitization template ID |
| **Security** | `MODEL_ARMOR_OUTPUT_TEMPLATE`, `SECURITY_OUTPUT_TEMPLATE_ID` | `"agentops-output-guard"` | Output sanitization template ID |
| **Security** | `MODEL_ARMOR_FAIL_CLOSED`, `MODEL_ARMOR_STRICT_CLOUD`, `SECURITY_FAIL_CLOSED` | `True` | Fail closed on Model Armor errors |
| **Security** | `SECURITY_PII_MASKING_ENABLED`, `SECURITY_JAILBREAK_THRESHOLD` | `True`, `0.80` | PII masking toggle & jailbreak confidence threshold (`0.0`–`1.0`) |
| **Database** | `DB_TYPE`, `DB_HOST`, `DB_PORT`, `DB_USERNAME`, `DB_PASSWORD`, `DB_DATABASE_NAME` | `"bigquery"`, `"127.0.0.1"`, `5432`, `""`, `SecretStr("")`, `"agentops"` | `DatabaseSettings` connection parameters (`DB_TYPE`: `snowflake \| bigquery \| postgres \| sap_hana \| databricks \| feature_store`) |
| **Database** | `DB_DATABRICKS_HTTP_PATH` (`DATABRICKS_HTTP_PATH`), `DB_DATABRICKS_TOKEN` (`DATABRICKS_TOKEN`) | `""`, `SecretStr("")` | Databricks SQL Warehouse HTTP path & token (`SecretStr`) |
| **Identity** | `AGENTOPS_IDENTITY__JWKS_URL`, `AGENTOPS_IDENTITY__CLIENT_ID`, `AGENTOPS_IDENTITY__ISSUER`, `ENTRA_*`, `JWT_PUBLIC_KEY`, `MCP_JWT_PUBLIC_KEY`, `AGENTOPS_IDENTITY__ALLOWED_JWKS_HOSTS` | `None` | SSRF-guarded RS256 JWKS URL, mandatory `aud`/`iss`, Entra ID aliases (single-tenant issuer derivation), static RSA PEM public key, and pinned JWKS host allowlist |
| **Identity** | `JWT_SECRET_KEY`, `ENVIRONMENT`, `DEV_RBAC_USER` | `None`, `""`, `""` | HS256 secret (`>=32` UTF-8 bytes outside dev); `ENVIRONMENT` gates fail-closed auth; `DEV_RBAC_USER` honored only in `dev \| development \| local \| test` |
| **Prompts** | `PROMPT_REGISTRY_BACKEND`, `PROMPT_REGISTRY_LOCAL_DIR` | `"local"`, `"prompts"` | Default prompt backend (`local \| git \| vertex_ai \| gcs`) |
| **Prompts** | `PROMPT_REGISTRY_GCS_BUCKET`, `PROMPT_REGISTRY_GCS_PREFIX`, `PROMPT_REGISTRY_PROJECT_ID`, `PROMPT_REGISTRY_LOCATION` | `None`, `"prompts"`, Inherits `GCPSettings` | GCS prompt storage & explicit GCP project/location overrides |
| **Prompts** | `PROMPT_REGISTRY_FALLBACK_TO_LOCAL`, `PROMPT_REGISTRY_FALLBACK_TO_GIT` | `False` | Explicit local YAML fallback toggle (plus legacy alias) |
| **Rate Limit** | `RATE_LIMIT_ENABLED`, `RATE_LIMIT_REQUESTS_PER_MINUTE`, `RATE_LIMIT_BURST_SIZE`, `RATE_LIMIT_MAX_CONCURRENCY`, `RATE_LIMIT_WAIT_TIMEOUT_SECONDS` | `True`, `120`, `30`, `25`, `0.0` | Default rate limiter parameters and bounded wait timeout |

---

## 5. Testing, Linting & Packaging

```bash
# Lint & format verification (line-length = 80, single-line sorted imports)
make lint

# Strict type checking
make typecheck

# Hermetic offline unit & contract suite (--eval-mode=in_memory)
make test

# Build wheel & sdist into dist/
make build-sdk
```
