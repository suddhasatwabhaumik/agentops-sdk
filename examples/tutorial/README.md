# AgentOps SDK Tutorials (`v1.0.0`): One Capability per Folder, Real GCP, Native OTLP

Each folder demonstrates one capability of `agentops-sdk` v1.0.0 with a concise `main.py` and a `README.md` documenting the exact SDK calls, GCP resources, and IAM permissions needed. Every tutorial calls real GCP services; nothing is mocked. The only local stack runs the OpenTelemetry collectors (`observability/stack.sh`), giving you a unified local view of spans, metrics, and logs.

```
                 your laptop                                   Google Cloud (<your-gcp-project-id>, europe-west4)
 ┌────────────────────────────────────────┐        ┌──────────────────────────────────────────────────────┐
 │ examples/tutorial/NN_*/main.py         │        │ Vertex AI: Gemini · Prompt Management · Evaluation   │
 │   └─ agentops_sdk  ──── GCP APIs ──────┼───────►│            Agent Engine (Memory Bank) · Feature Store│
 │        │                               │        │ BigQuery · Secret Manager · Model Armor · DLP        │
 │        │ OTLP gRPC (traces, metrics,   │        │ Cloud SQL (PostgreSQL)                               │
 │        ▼        logs) localhost:4317   │        └──────────────────────────────────────────────────────┘
 │ Alloy ─► Tempo · Prometheus · Loki     │
 │ Grafana http://localhost:3002          │   ✓ mode="otlp": zero Cloud Trace / Cloud Logging side-writes
 └────────────────────────────────────────┘
```

## Start here

```bash
cd <repo root>
pip install -e ".[all]"                                  # or: pip install -e ".[dev,all]"
cp examples/tutorial/.env.example examples/tutorial/.env  # project, region, model, endpoints
gcloud auth application-default login
bash examples/tutorial/observability/stack.sh up          # Alloy, Tempo, Prometheus, Loki, Grafana
python examples/tutorial/00_setup/preflight.py            # what is ready vs missing permissions
python examples/tutorial/01_tracing_and_finops/main.py    # first tutorial
```

Every script prints a `run_id`. In Grafana (anonymous viewer, dashboard **AgentOps SDK - Overview**) filter `instance = <run_id>` to isolate your run.

## The tutorials

| # | Folder | You learn | GCP services | Extra IAM beyond `aiplatform.user`? |
|---|---|---|---|---|
| 01 | `01_tracing_and_finops` | Agent flow spans, LLM token cost per KPI | Vertex AI Gemini | No |
| 02 | `02_case_telemetry` | Business records per use case in BigQuery | BigQuery, Cloud Logging | BigQuery write (`roles/bigquery.dataEditor`) |
| 03 | `03_rate_limiting` | Keyed quotas & concurrency caps as metrics | None | No |
| 04 | `04_tool_output_validation` | Schema contracts for tools & LLM JSON, retries | Vertex AI Gemini | No |
| 05 | `05_sql_guard` | LLM-written SQL allowed only if the AST is safe | BigQuery, Gemini | Dataset/table from `00_setup` |
| 06 | `06_row_level_security` | Same SQL, each user sees only their rows | BigQuery | Dataset/table from `00_setup` |
| 07 | `07_jwt_auth` | Sign & verify tokens with a key in Secret Manager | Secret Manager | Optional write (`TUTORIAL_STORE_TOKEN=1`) |
| 08 | `08_identity_and_secrets` | Caller identity across nested calls, secret injection | Secret Manager | Optional write (`TUTORIAL_STORE_SECRET=1`) |
| 09 | `09_safety_model_armor` | Fail-closed Cloud Model Armor block & mask rules | Model Armor | Pre-provisioned templates (`roles/modelarmor.user` + `viewer`) |
| 10 | `10_privacy_dlp` | De-identify PII in output via regional Cloud DLP | Cloud DLP | API + `roles/dlp.user` |
| 11 | `11_prompt_registry` | Versioned prompts in Vertex AI Prompt Management | Vertex AI | No |
| 12 | `12_memory` | Session turns in Cloud SQL, facts in Memory Bank | Agent Engine, Cloud SQL | Engine & Cloud SQL from `00_setup` |
| 13 | `13_evaluation_quality_gates` | Groundedness/relevance scoring & pytest gate | Vertex AI Evaluation | No |
| 14 | `14_audit_ledger` | Immutable BigQuery audit rows per agent action | BigQuery | BigQuery write (`roles/bigquery.dataEditor`) |
| 15 | `15_feature_store_sync` | BigQuery features synced & served online | BigQuery, Feature Store | BigQuery write + Vertex Feature Store |

`00_setup/` holds `preflight.py` (diagnoses missing APIs/roles/resources) and `provision.py` (provisions datasets/tables/Agent Engine/Cloud SQL idempotently). See [`ACCESS.md`](ACCESS.md) for the exact IAM role matrix.

## Telemetry Routing & Fail-Closed Guarantees

`agentops-sdk` v1.0.0 enforces these defaults out of the box:

- **Isolated Local OTLP Routing**: [`_shared/telemetry.py`](_shared/telemetry.py) calls `configure_telemetry(mode="otlp", service_name=..., collector_url=...)`. In `otlp` mode, traces, metrics, and structured logs (via `OtlpStdlibLogHandler`) flow exclusively to `OTEL_COLLECTOR_URL`; FinOps Cloud Logging writes only occur when `mode` is `gcp` or `hybrid`.
- **Fail-Closed GCP Adapters (Zero Silent Fallbacks)**: `SecurityGuard` / `CloudModelArmorScanner` (`fail_closed=True`), `OutputDLPSanitizer` / `GoogleDLPScrubber`, `BigQueryAuditAdapter`, `CloudSQLSessionMemory`, `VertexMemoryBank`, `VertexEvalTaskAdapter`, `PromptRegistry` (`fallback_to_local=False`), and `FeatureStoreAdminClient` raise explicit typed errors rather than silently substituting offline mocks or local `.jsonl`/SQLite files.
- **Canonical Memory & Keyed Rate Limits**: `VertexMemoryBank` writes and reads share canonical `scope={"user_id": user_id}` with native `search_query` similarity search (`ADR-4`), and `AsyncRateLimiter` keys buckets by `(rule_id, key)`.

## Conventions

- `settings.load()` runs before importing SDK runtime modules so `.env` overrides take effect cleanly.
- No tutorial deletes shared GCP resources.
- Every `main.py` exits non-zero if a backing GCP service call fails, making every tutorial usable as a smoke test.

## Execution Matrix & Prerequisites

All 15 tutorials align cleanly with `agentops-sdk` v1.0.0 against `<your-gcp-project-id>` in `europe-west4`:

| Tutorials | Services Exercised | Prerequisite / Check (`00_setup/preflight.py`) |
|---|---|---|
| `01`, `03`, `04`, `11`, `13` | Gemini, Rate Limiter, Tool Validation, Vertex Prompts, Vertex Evaluation (`global` judge default `gemini-2.5-flash`) | Vertex AI User (`roles/aiplatform.user`) |
| `07`, `08` | Secret Manager + JWT / Identity | Secret Manager read; set `TUTORIAL_STORE_TOKEN=1` / `TUTORIAL_STORE_SECRET=1` on first run if secrets are empty |
| `02`, `05`, `06`, `14`, `15` | BigQuery Case Telemetry, SQL Guard, RLS, Audit Ledger, Feature Store | `roles/bigquery.dataEditor`, `roles/bigquery.jobUser`, and `00_setup/provision.py bigquery` (`agentops_tutorial` dataset) |
| `09` | Cloud Model Armor (`agentops-input-guard` / `agentops-output-guard`) | Centrally provisioned templates + `roles/modelarmor.user` & `roles/modelarmor.viewer` |
| `10` | Cloud DLP (`GoogleDLPScrubber`) | `dlp.googleapis.com` enabled + `roles/dlp.user` |
| `12` | Vertex AI Memory Bank & Cloud SQL | `00_setup/provision.py agent-engine` + `sqladmin.googleapis.com` + Cloud SQL instance (`roles/cloudsql.client`) |
