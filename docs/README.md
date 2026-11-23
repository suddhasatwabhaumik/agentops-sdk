<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# AgentOps Governance Framework - Technical Design Document (TDD) Index

**Platform Version:** 1.0.0 (Hexagonal Ports & Adapters Architecture)  
**Status:** Active Production Standard  
**Branch:** `main`  

Welcome to the Technical Design Document index for the AgentOps SDK (`agentops-sdk`) and decoupled MCP Governance Gateway. This documentation covers the design, architecture, implementation strategies, fail-closed security controls, and FinOps/observability standards for standardizing, securing, and tracing Agentic AI applications on Google Cloud Platform.

---

## Document Index

> [!TIP]
> **Release Baseline**: Review the [v1.0.0 Release Notes](../releases/v1.0.0_agentops_sdk_initial_release.md) for the canonical 12-subpackage inventory, default Cloud Logging behavior, and configuration schemas.

Please read through the following specifications for in-depth details of each subsystem:

1.  ### [Async Rate Limiting, Engine Concurrency Sizing & Zero-Trust Security](async_concurrency_and_rate_limiting_guide.md)
    *   **Async & Sync Rate Limiting**: `AsyncRateLimiter` and `SyncRateLimiter` with `RateLimitAlgorithm.TOKEN_BUCKET` and `SLIDING_WINDOW`, concurrency semaphores, and keyed rule registries.
    *   **Bounded Execution & Back-Pressure**: Non-blocking fail-fast or bounded `wait_timeout_seconds` queuing raising `RateLimitExceededError` with retry metadata.
    *   **Engine Concurrency Sizing**: Concurrency isolation and gateway capacity patterns for BigQuery and downstream data services.
2.  ### [Security, Identity & Verification Plan](security_and_verification.md)
    *   **Zero-Trust Authentication & JWKS SSRF Guard (`pyjwt>=2.10.0`)**: Mandatory Bearer tokens via `JWTAuthEngine`; `RS256` verification with mandatory `audience` + `issuer`, static RSA PEM `public_key` (`JWT_PUBLIC_KEY` / `MCP_JWT_PUBLIC_KEY`), Entra ID env aliases (`ENTRA_*`) with single-tenant issuer derivation, `"tid"` -> `"tenant_id"` RLS mapping, and `validate_jwks_url()` SSRF defense.
    *   **Zero Hardcoded Secrets & Gateway Capacity**: Fail-closed `SecretManagerStore` lookup, `BatchSpanProcessor` + `MeterProvider`, and per-loop lock isolation.
    *   **Scope-Aware AST Row-Level Security (`sqlglot>=30.0.0`)**: `RowLevelPolicyEngine` with `LEFT JOIN ... ON`, dialect-normalized CTEs, sole-`FROM` `ML.PREDICT`, `TableFromRows`/`PIVOT`/`UNPIVOT`/column-list alias rejection, post-rewrite verification, and role-gated admin bypass (`role in {"admin", "superadmin"}`).
    *   **Boundary-Anchored SQL AST Guard**: `SQLASTValidator` wildcard table allowlists (`dataset.*`), qualified table verification, and comment/DDL/DML blocking.
3.  ### [SDK Library & Architecture Specifications](sdk_design.md)
    *   **Hexagonal Ports & Adapters**: Pure core (`domain/`, `ports/`, `config/`) separated from 9 cohesive feature subpackages (`telemetry`, `audit`, `memory`, `evaluation`, `prompts`, `security`, `sql`, `resilience`, `feature_store`).
    *   **Root `pyproject.toml` Packaging**: Granular extras (`cloudsql`, `eval`, `ragas`, `feature-store`, `mcp`, `all`, `dev`) and PEP 562 lazy root exports (`from agentops_sdk import ...`) avoiding eager heavy cloud imports.
    *   **Dependency Injection via Protocols**: Typed `ports/` protocols enabling direct injection of GCP production adapters or explicit `InMemory*` / `Local*` doubles without hidden fallbacks.
    *   **Unified OpenTelemetry Configuration**: `configure_telemetry(mode=...)` supporting `auto`, `gcp`, `otlp`, `hybrid`, and `off` with native `OtlpStdlibLogHandler` bridging.
4.  ### [Model Armor Security Layer Guide](model_armor_security_layer_guide.md)
    *   **Fail-Closed Content Guardrails**: `SecurityGuard` and `CloudModelArmorScanner` (`fail_closed=True`) with prompt-injection, jailbreak, and SDP threat filtering.
5.  ### [Output GDPR & Cloud DLP Sanitization Design](privacy_dlp_design.md)
    *   **Regional PII Scrubbing**: `OutputDLPSanitizer` and `GoogleDLPScrubber` targeting regional Cloud DLP endpoints (`dlp.{location}.rep.googleapis.com`).
6.  ### [Tool Output Validation Guide](tool_output_validation_guide.md)
    *   **Schema & Retry Contracts**: `@validate_tool_output` Pydantic schema enforcement, custom assertion callbacks, and exponential backoff with jitter.
7.  ### [Prompt Registry Guide](prompt_registry_guide.md)
    *   **Semver Prompt Governance**: `PromptRegistry` managing `PromptTemplate` manifests across `LocalYAMLPromptAdapter`, `GCSPromptAdapter`, `VertexAIPromptAdapter`, and `InMemoryPromptAdapter`.
    *   **Sandboxed Interpolation & Explicit Fallback**: Bounded Jinja2 `SandboxedEnvironment` rendering (`render_prompt`, `format_prompt`) with opt-in `fallback_adapter` / `fallback_to_local=False` default.
8.  ### [System Architecture & Networking](architecture_and_networking.md)
    *   **System Architecture Overview**: Layout of client query routes, FastMCP server (`mcp_servers/src/server.py`), BigQuery analytical lakehouse, Vertex AI Feature Store, and Vertex AI Agent Engine runtime.
    *   **Networking & Security Perimeter**: Cloud NAT, Private Google Access (PGA), Private Service Connect (PSC), and `SecretManagerStore`.
9.  ### [CI/CD Quality Gates, Build Pipelines & Monitoring Correlation](cicd_quality_gates_pytest_integration.md)
    *   **Pytest Plugin Options & Assertion Helpers**: `--eval-mode=in_memory | heuristic | vertex` (default `in_memory`), `--fail-under-*` threshold flags, `assert_quality_gate` / `assert_trajectory_quality_gate` (`QualityGateError`), and Markdown job summary reports.
    *   **Hardened CI/CD Workflows & FinOps Correlation**: Immutable 40-SHA GitHub Action pins + keyless Workload Identity Federation (`examples/cicd_templates/agent_quality_gates.yml`, `.github/workflows/build-wheel.yml`), digest-pinned Google Cloud Build (`cloudbuild.yaml`), and OpenTelemetry / Cloud Logging (`ADR-2`) BigQuery FinOps billing correlation.
10. ### [Vertex AI Gen AI & ADK Evaluator Guide](vertex_ai_evaluator_guide.md)
    *   **Unified Evaluation Suite**: `InMemoryEvaluator`, `HeuristicEvaluator`, `RagasEvaluator`, `VertexEvalTaskAdapter`, `CompositeEvaluator`, and `EvaluatorRegistry`.
    *   **Gemini 3.8 Flash & Quality Gate Telemetry**: `gemini-3.8-flash` default judge/simulator model (`AGENTOPS_EVAL_AUTORATER_MODEL`, full publisher URI + `judge_location="global"`), `AutoraterConfig` preview routing, and automatic `EvaluationResult.explanations` / `raw_scores` / `console_url` surfacing.
11. ### [Model Context Protocol (MCP) Integration](mcp_integration.md)
    *   **FastMCP Server & Tool Routing**: Containerized MCP server (`python -m mcp_servers.src.server`) exposing `query_database_tool`, `query_databricks_tool`, and `query_feature_store_tool`.
    *   **Fail-Closed Header Auth & AST Enforcement**: ASGI `HeaderBindingMiddleware` extracting HTTP `Authorization: Bearer` headers (no LLM-exposed `auth_token` parameter), `MCP_REQUIRE_AUTH=true` default, SQL AST + RLS + entity-type allowlists.
12. ### [Copilot Studio & API Gateway Wrapper](copilot_studio_gateway.md)
    *   **Gateway Integration**: Patterns for exposing safety (`SecurityGuard`), privacy (`OutputDLPSanitizer`), audit (`AuditLedger`), and owner-scoped session memory endpoints (`_require_owner_or_admin`, sanitized `401`/`403` errors) behind Cloud Armor WAF and Serverless VPC Access.
13. ### [Vertex AI Feature Online Store Synchronization & Serving Framework](vertex_ai_feature_store_sync.md)
    *   **Modern Feature Store Serving**: Links BigQuery offline tables to Bigtable-backed Feature Online Stores in `europe-west4` for sub-10ms online reads.
    *   **Developer Python SDK (`FeatureStoreAdminClient` & `FeatureStoreOnlineClient`)**: Provisioning (`ensure_online_store`, `ensure_feature_view`), sync triggering (`trigger_sync`), and runtime reads (`read_online_features`).
14. ### [Conversational Session & Long-Term Memory Architecture Guide](session_and_long_term_memory_guide.md)
    *   **Port Contracts (`SessionMemoryPort`, `LongTermMemoryPort`)**: Enforces strict session/user isolation and sync/async parity across backends.
    *   **Cloud SQL Relational Backend (`CloudSQLSessionMemory`)**: SQLAlchemy Core engine with `(session_id, id)` B-tree indexing, keyset (`after_id`, `before_id`) and offset pagination via `get_messages()`, `DEFAULT_PAGE_SIZE = 50`, `MAX_PAGE_SIZE = 200`.
    *   **Vertex AI Memory Bank (`VertexMemoryBank`)**: Persistent cross-session facts using canonical `scope={"user_id": user_id}`, optional `memory_type` filtering via bounded single-RPC over-fetch (`min(max(limit * 4, 20), 100)`), native `search_query` similarity recall, and explicit `InMemory*` test doubles.
15. ### [Universal Trace, OpenTelemetry & FinOps Telemetry Guide](universal_trace_and_finops_guide.md)
    *   **Unified OpenTelemetry Provider (`configure_telemetry`)**: `auto`, `gcp`, `otlp`, `hybrid`, and `off` modes plus native `OtlpStdlibLogHandler` bridging.
    *   **FinOps & Token Instrumentation**: `@trace_agent_flow`, `@trace_agent_span`, `@trace_llm_call`, `log_llm_metrics`, `emit_finops_telemetry`, and `agentops.*` / `business.*` / `llm.*` / `gen_ai.*` span attributes.
16. ### [BigQuery & GCS Observability & Per-Case Telemetry Architecture Guide](bigquery_gcs_observability_and_case_telemetry_guide.md)
    *   **Per-Case Telemetry**: Tracks per-case transaction counts, stage timing maps (`stages: dict[str, float]`), business KPIs, and P&L impact lines via `UseCaseTracker` and `@trace_use_case`.
    *   **Single-Stream Cloud Logging Default (ADR-2)**: Defaults to `CloudLoggingTelemetryExporter` (`agentops-use-case-telemetry`) while supporting explicit opt-in `BigQueryTelemetryExporter`, `BatchedGCSTelemetryExporter`, `CompositeTelemetryExporter`, `LocalFileTelemetryExporter`, and `InMemoryTelemetryExporter`.
17. ### [Immutable Audit Ledger Architecture Design](audit_ledger_design.md)
    *   **Zero-Overhead Audit Default (ADR-2)**: `AuditLedger` defaults to `CloudLoggingAuditAdapter` (`agentops-audit-ledger`) over `AuditSinkPort` so Cloud Log Router handles asynchronous BigQuery archival.
    *   **Direct BigQuery & Opt-In Local Sinks**: Supports explicit `BigQueryAuditAdapter(dataset_id="agentops_governance", table_id="audit_ledger")` with bounded exponential `Retry`, day partitioning, `LocalFileAuditAdapter` (`0o600` + `O_NOFOLLOW`), and `InMemoryAuditAdapter` with zero silent fallbacks.
