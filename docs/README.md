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
    *   **Zero-Trust Authentication & JWKS SSRF Guard**: Mandatory Bearer tokens via `JWTAuthEngine`; corporate Microsoft Entra ID RS256 JWKS verification hardened with `validate_jwks_url()` (`https://` only, blocking loopback/private/link-local/metadata IPs, plus `AGENTOPS_IDENTITY__ALLOWED_JWKS_HOSTS`).
    *   **Zero Hardcoded Secrets**: Fail-closed `SecretManagerStore` lookup without insecure defaults or secret leaks.
    *   **Role-Gated AST Row-Level Security & Precedence**: `sqlglot` parse-tree rewriting in `RowLevelPolicyEngine` with fail-closed dialect parsing, role-verified admin bypass (`role in {"admin", "superadmin"}`), and parenthesized compound predicate safety.
    *   **Boundary-Anchored SQL AST Guard**: `SQLASTValidator` wildcard table allowlists (`dataset.*`), qualified table verification, and comment/DDL/DML blocking.
    *   **Fail-Closed Cloud Model Armor & PII Telemetry Redaction**: `SecurityGuard` / `CloudModelArmorScanner` defaulting to `fail_closed=True` and `OutputDLPSanitizer` PII scrubbing.
3.  ### [SDK Library & Architecture Specifications](sdk_design.md)
    *   **Hexagonal Ports & Adapters**: Pure core (`domain/`, `ports/`, `config/`) separated from 9 cohesive feature subpackages (`telemetry`, `audit`, `memory`, `evaluation`, `prompts`, `security`, `sql`, `resilience`, `feature_store`).
    *   **Root `pyproject.toml` Packaging**: Granular extras (`cloudsql`, `eval`, `ragas`, `feature-store`, `mcp`, `all`, `dev`) and PEP 562 lazy root exports (`from agentops_sdk import ...`) avoiding eager heavy cloud imports.
    *   **Dependency Injection via Protocols**: Typed `ports/` protocols enabling direct injection of GCP production adapters or explicit `InMemory*` / `Local*` doubles without hidden fallbacks.
    *   **Unified OpenTelemetry Configuration**: `configure_telemetry(mode=...)` supporting `auto`, `gcp`, `otlp`, `hybrid`, and `off` with native `OtlpStdlibLogHandler` bridging.
4.  ### [Prompt Registry Guide](prompt_registry_guide.md)
    *   **Semver Prompt Governance**: `PromptRegistry` managing `PromptTemplate` manifests across `LocalYAMLPromptAdapter`, `GCSPromptAdapter`, `VertexAIPromptAdapter`, and `InMemoryPromptAdapter`.
    *   **Sandboxed Interpolation & Explicit Fallback**: Bounded Jinja2 `SandboxedEnvironment` rendering (`render_prompt`, `format_prompt`) with opt-in `fallback_adapter` / `fallback_to_local=False` default.
5.  ### [System Architecture & Networking](architecture_and_networking.md)
    *   **System Architecture Overview**: Layout of client query routes, MCP server (`mcp_servers/src/server.py`), BigQuery analytical lakehouse, Vertex AI Feature Store, and Vertex AI Agent Engine runtime.
    *   **Data Flow & Sync Pipeline**: End-to-end topology for batch sync operations from BigQuery lakehouse tables to online feature serving.
6.  ### [CI/CD & Monitoring Integration](cicd_and_monitoring.md)
    *   **CI/CD Automation Templates**: Modular GitHub Actions workflows (`examples/cicd_templates/agent_quality_gates.yml`) and Google Cloud Build (`cloudbuild.yaml`).
    *   **CI/CD Quality Gates**: Pluggable `EvaluatorPort` adapters (`InMemoryEvaluator`, `HeuristicEvaluator`, `RagasEvaluator`, `VertexEvalTaskAdapter`) and pytest gate enforcement.
    *   **FinOps & Trace Routing**: OpenTelemetry correlation tags (`agentops.*`, `business.*`, `llm.*`), Cloud Logging sinks, and Looker Studio cost dashboarding.
7.  ### [CI/CD Quality Gates & Pytest Plugin Integration](cicd_quality_gates_pytest_integration.md)
    *   **Pytest Plugin Options**: `--eval-mode=in_memory | heuristic | vertex` (default `in_memory`), `--fail-under-*` threshold flags, and Markdown job summary reports.
    *   **Assertion Helpers**: `assert_quality_gate` and `assert_trajectory_quality_gate` raising `QualityGateError` on threshold violations.
8.  ### [Vertex AI Gen AI & ADK Evaluator Guide](vertex_ai_evaluator_guide.md)
    *   **Unified Evaluation Suite**: `InMemoryEvaluator`, `HeuristicEvaluator` (with regex/keyword/custom rules), `RagasEvaluator`, `VertexEvalTaskAdapter`, `CompositeEvaluator`, and `EvaluatorRegistry`.
    *   **Gen AI & ADK Capabilities**: `ExperimentEngine`, `CustomRubricMetric`, `generate_conversation_scenarios`, `simulate_agent_conversations`, AST-guarded `register_code_execution_metric`, `register_llm_metric`, `generate_loss_clusters`, and `evaluate_agent_engine`.
9.  ### [Model Context Protocol (MCP) Integration](mcp_integration.md)
    *   **FastMCP Server & Tool Routing**: Containerized MCP server (`python -m mcp_servers.src.server`) exposing `query_database_tool`, `query_databricks_tool`, and `query_feature_store_tool`.
    *   **Fail-Closed Header Auth & AST Enforcement**: ASGI `HeaderBindingMiddleware` extracting HTTP `Authorization: Bearer` headers (no LLM-exposed `auth_token` parameter), `MCP_REQUIRE_AUTH=true` default, SQL AST + RLS + entity-type allowlists.
10. ### [AWS to GCP Migration Guide](aws_to_gcp_migration_guide.md)
    *   **Architecture Mapping**: Technical blueprint mapping AWS services (EC2, ECS, Redshift, vector/memory stacks) to native GCP equivalents (Vertex AI Agent Engine, Cloud Run, BigQuery, Cloud SQL, Vertex AI Memory Bank).
11. ### [Copilot Studio & API Gateway Wrapper](copilot_studio_gateway.md)
    *   **Gateway Integration**: Patterns for exposing safety (`SecurityGuard`), privacy (`OutputDLPSanitizer`), audit (`AuditLedger`), and session memory endpoints behind Cloud Armor WAF and Serverless VPC Access.
12. ### [Cross-Platform Multi-Agent Orchestration](cross_platform_orchestration.md)
    *   **Interoperability Patterns**: Hub-and-Spoke, Gateway Wrapper, and Event-Driven Pub/Sub messaging with W3C `traceparent` propagation.
13. ### [RECOMMENDED: Enterprise MCP Golden Path, Centralized AgentHub & Solution Orchestration Blueprint](recommendations/mcp_hub_and_golden_path_architecture.md)
    *   **Unified Blueprint**: Synthesizes end-to-end multi-agent orchestration (Enterprise Agent Platform, A2A protocol, enterprise data sources, Databricks Lakehouse) with the centralized AgentHub gateway control plane.
    *   **The MCP Golden Path**: End-to-end 6-stage lifecycle (`Scaffold -> Test -> Secure -> Register -> Deploy -> Monitor`) for authoring production-grade MCP tools.
    *   **Zero-Trust Security Perimeter**: Cloud Armor WAF, Private Service Connect (PSC), Cloud DLP sanitization, and Secret Manager dynamic TTL caching.
14. ### [RECOMMENDED: Enterprise Agent Registry & Sharing Catalog (Enterprise Agent Platform / Vertex AI)](recommendations/agent_registry_design.md)
    *   **Enterprise Agent Platform & Vertex AI Engine**: Unified agent catalog, serverless Agent Engine deployment, and Vertex Memory Bank binding.
    *   **Lifecycle & Promotion Gates**: Stage aliasing (`@dev`, `@staging`, `@canary`, `@prod`) with automated `VertexEvalTaskAdapter` quality benchmarks.
15. ### [RECOMMENDED: Governance-as-Code (GoC) Golden Pathway](recommendations/governance_as_code_pathway.md)
    *   **Governance Lifecycle**: Standardized SDK instrumentation, build-time CI/CD quality gates, and runtime safety/DLP/audit shields.
16. ### [RECOMMENDED: Enterprise Agent Skill Registry & Tool Catalog (Enterprise Agent Platform / Vertex AI)](recommendations/skill_registry_design.md)
    *   **Multi-Protocol Skill Catalog**: Python wheels, MCP endpoints, OpenAPI extensions, and delegated `@pass_user_identity()` / `@validate_tool_output` guardrails.
17. ### [RECOMMENDED: Enterprise Data Hub Integration Blueprint: MCP Virtualization vs. BigQuery Migration](recommendations/edh_integration_mcp_vs_bigquery_migration.md)
    *   **Tradeoff Matrix**: Evaluates federated MCP pushdown against CDC ingestion into BigQuery with hot/cold tiering guidance.
18. ### [Databricks to BigQuery Native Delta Architecture & Serving Strategies](databricks_bigquery_connector_architecture.md)
    *   **First-Party Delta Lake Support via BigLake**: Direct `_delta_log` parsing, Deletion Vectors, Liquid Clustering, semantic layer metrics, and 4 developer data-access patterns.
19. ### [Sovereign BigQuery & BigLake Implementation Plan with Semantic Layer & Feature Store](revised_data_connector_implementation_plan.md)
    *   **Consolidated Lakehouse Reference**: Architectural blueprint for transitioning legacy data connectors to BigLake Native Delta, BigQuery semantic views, and Vertex AI Feature Store with `agentops_sdk` governance.
20. ### [Vertex AI Feature Online Store Synchronization & Serving Framework](vertex_ai_feature_store_sync.md)
    *   **Modern Feature Store Serving**: Links BigQuery offline tables to Bigtable-backed Feature Online Stores in `europe-west4` for sub-10ms online reads.
    *   **Developer Python SDK (`FeatureStoreAdminClient` & `FeatureStoreOnlineClient`)**: Provisioning (`ensure_online_store`, `ensure_feature_view`), sync triggering (`trigger_sync`), and runtime reads (`read_online_features`).
    *   **CLI Migration Toolkit (`tools/feature_store_migration/`)**: Modular module scripts (`python -m tools.feature_store_migration.step2_setup_feature_store`, etc.).
21. ### [Conversational Session & Long-Term Memory Architecture Guide](session_and_long_term_memory_guide.md)
    *   **Port Contracts (`SessionMemoryPort`, `LongTermMemoryPort`)**: Enforces strict session/user isolation and sync/async parity across backends.
    *   **Cloud SQL Relational Backend (`CloudSQLSessionMemory`)**: SQLAlchemy Core engine with `(session_id, id)` B-tree indexing, keyset (`after_id`, `before_id`) and offset pagination via `get_messages()`, `DEFAULT_PAGE_SIZE = 50`, `MAX_PAGE_SIZE = 200`.
    *   **Vertex AI Memory Bank (`VertexMemoryBank`)**: Persistent cross-session facts using canonical `scope={"user_id": user_id}`, metadata/type persistence, native `search_query` similarity recall, and explicit `InMemory*` test doubles.
    *   **CompositeMemory Coordination**: Unified `get_context()` / `aget_context()` with fail-closed `strict=True` default.
22. ### [Universal Trace, OpenTelemetry & FinOps Telemetry Guide](universal_trace_and_finops_guide.md)
    *   **Unified OpenTelemetry Provider (`configure_telemetry`)**: `auto`, `gcp`, `otlp`, `hybrid`, and `off` modes plus native `OtlpStdlibLogHandler` bridging.
    *   **FinOps & Token Instrumentation**: `@trace_agent_flow`, `@trace_agent_span`, `@trace_llm_call`, `log_llm_metrics`, `emit_finops_telemetry`, and `agentops.*` / `business.*` / `llm.*` / `gen_ai.*` span attributes.
23. ### [BigQuery & GCS Observability & Per-Case Telemetry Architecture Guide](bigquery_gcs_observability_and_case_telemetry_guide.md)
    *   **Per-Case Telemetry**: Tracks per-case transaction counts, stage timing maps (`stages: dict[str, float]`), business KPIs, and P&L impact lines via `UseCaseTracker` and `@trace_use_case`.
    *   **Single-Stream Cloud Logging Default (ADR-2)**: Defaults to `CloudLoggingTelemetryExporter` (`agentops-use-case-telemetry`) while supporting explicit opt-in `BigQueryTelemetryExporter`, `BatchedGCSTelemetryExporter`, `CompositeTelemetryExporter`, `LocalFileTelemetryExporter`, and `InMemoryTelemetryExporter`.
    *   **Log Router CLI**: Module invocation via `python -m tools.observability.log_router_admin` and shell helpers in `tools/observability/`.
24. ### [Immutable Audit Ledger Architecture Design](audit_ledger_design.md)
    *   **Zero-Overhead Audit Default (ADR-2)**: `AuditLedger` defaults to `CloudLoggingAuditAdapter` (`agentops-audit-ledger`) over `AuditSinkPort` so Cloud Log Router handles asynchronous BigQuery archival.
    *   **Direct BigQuery & Opt-In Local Sinks**: Supports explicit `BigQueryAuditAdapter(dataset_id="agentops_governance", table_id="audit_ledger")` with bounded exponential `Retry`, day partitioning, `LocalFileAuditAdapter` (`0o600` + `O_NOFOLLOW`), and `InMemoryAuditAdapter` with zero silent fallbacks.
