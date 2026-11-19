<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# SDK Development & Implementation Plan (`v1.0.0`)

This document maps the completed engineering phases in `agentops-sdk==1.0.0` (`pyproject.toml`, branch `main`) to the canonical 12-subpackage hexagonal architecture under `sdk/src/agentops_sdk/`, the hardened MCP governance gateway (`mcp_servers/src/`), operational CLI tooling (`tools/`), and CI/CD pipelines.

> [!NOTE]
> **Hexagonal Architecture (`v1.0.0`)**: All Phase 1–4 deliverables listed below are implemented across the canonical 12-subpackage hexagonal structure under `sdk/src/agentops_sdk/`. See the [v1.0.0 Release Notes](../releases/v1.0.0_agentops_sdk_initial_release.md).

---

## Task Checklists by Phase

### Phase 1: Domain Contracts, Observability & Security Foundation
- [x] **Domain Contracts & Scope**: Define immutable domain models (`TelemetryContext`, `LLMUsageMetric`, `UseCaseTelemetry`, `AuditLogEntry`, `ConversationTurn`, `MemoryItem`, `SafetyScanResult`, `SecurityVerdict`, `DecisionResult`, `SQLValidationResult`, `RateLimitRule`, `RateLimitResult`), typed `AgentOpsError` hierarchy, `ExecutionScope` (`bind_execution_scope`), `_BoundedSandboxEnvironment`, and input/log sanitization in `sdk/src/agentops_sdk/domain/` and `sdk/src/agentops_sdk/ports/`.
- [x] **OTel Telemetry & Per-Case Tracking**: Implement `configure_telemetry()`, `@trace_agent_flow`, `@trace_agent_span`, `@trace_llm_call`, `FinOpsCostCalculator`, and `UseCaseTracker` (`BigQueryTelemetryExporter`, `GCSTelemetryExporter`, `CloudLoggingTelemetryExporter`, rotating `LocalFileTelemetryExporter`) in `sdk/src/agentops_sdk/telemetry/`.
- [x] **Model Armor Security Guard**: Implement `SecurityGuard`, `CloudModelArmorScanner` (`fail_closed=True` default, regional GAPIC endpoints, SDP inspect fallback), and explicit `LocalRuleSafetyScanner` in `sdk/src/agentops_sdk/security/guard.py` and `sdk/src/agentops_sdk/security/model_armor.py`.
- [x] **Quality Evaluation & CI Gates**: Implement `VertexEvalTaskAdapter`, `RagasEvaluator`, `InMemoryEvaluator`, `HeuristicEvaluator`, `validate_metric_code` (strict AST allowlist), `assert_quality_gate`, `assert_trajectory_quality_gate`, and HTML/Markdown reporting in `sdk/src/agentops_sdk/evaluation/` plus the `agentops_eval` pytest plugin (`sdk/src/agentops_sdk/pytest_plugin.py`).

### Phase 2: MCP Governance Gateway, SQL AST Guard & Feature Store Sync
- [x] **Hardened FastMCP Governance Gateway**: Implement `mcp_servers/src/server.py`, `_http.py` (`HeaderBindingMiddleware`), and `auth_guard.py` with fail-closed `MCP_REQUIRE_AUTH=true`, Bearer token auth checked prior to AST validation, HTTPS `CONNECTOR_API_URL` validation, tenant-scoped `authorize_feature_store_tenant`, and zero LLM-exposed `auth_token` parameters.
- [x] **SQL Governance & Tenant RLS**: Implement `SQLASTValidator` (`validate_sql_query`, `sqlglot` AST parsing, `UNNEST`-only TVF allowlist, system schema blocking) and `RowLevelPolicyEngine` (`apply_row_level_policy` with recursive subquery/sampling unwrapping) in `sdk/src/agentops_sdk/sql/`.
- [x] **Vertex AI Feature Store (v2) Clients & Batch Tooling**: Implement `FeatureStoreAdminClient`, `FeatureStoreOnlineClient` (`WhichOneof("value")` decoding, 64-stripe bounded cache), and `InMemoryFeatureOnlineStore` in `sdk/src/agentops_sdk/feature_store/`, paired with `tools/feature_store_migration/step1..step5` and `sync_orchestrator.py`.

### Phase 3: Memory, Prompts, Auth, Rate Limiting & CI/CD
- [x] **Persistent Memory Adapters**: Implement `VertexMemoryBank` and `CloudSQLSessionMemory` in `sdk/src/agentops_sdk/memory/`.
- [x] **Prompt Registry & Sandboxed Templates**: Implement `PromptRegistry` (YAML frontmatter + semver + Vertex AI Prompt Management) using `_BoundedSandboxEnvironment` in `sdk/src/agentops_sdk/prompts/`.
- [x] **Resilience & Rate Limiting**: Implement `@validate_tool_output` (`ToolValidationError`), `AsyncRateLimiter`, `SyncRateLimiter`, `RateLimiterCore` (`TOKEN_BUCKET`, `SLIDING_WINDOW`, `CONCURRENCY_SEMAPHORE`), and `@rate_limit` in `sdk/src/agentops_sdk/resilience/`.
- [x] **Zero-Trust Identity & Secret Caching**: Implement `JWTAuthEngine` (`SafeJWKClient` SSRF guard, PBKDF2 600k iterations, pinned algs), `@pass_user_identity`, `@inject_secret`, and 256-stripe `SecretManagerStore` in `sdk/src/agentops_sdk/security/`.
- [x] **CI/CD Pipelines & Quality Gates**: Provide `.github/workflows/build-wheel.yml`, root `cloudbuild.yaml` (OPA Rego + pytest unit/contract + wheel/Twine check), `Makefile`, and `examples/cicd_templates/agent_quality_gates.yml`.

### Phase 4: Audit Ledger, GDPR DLP Privacy & 23-CWE Security Hardening
- [x] **Multi-Sink Compliance Audit Ledger**: Implement `AuditLedger` (`CloudLoggingAuditAdapter` default, `BigQueryAuditAdapter`, `MultiSinkAuditAdapter`, `O_NOFOLLOW`/`0o600` rotating `LocalFileAuditAdapter`) in `sdk/src/agentops_sdk/audit/` and operational log router tools under `tools/observability/`.
- [x] **GDPR Cloud DLP Sanitizer**: Implement `OutputDLPSanitizer`, `GoogleDLPScrubber` (fail-closed `PrivacyScrubError`, `dlp.{location}.rep.googleapis.com`, 400KB UTF-8 chunking), and `RegexPrivacyScrubber` in `sdk/src/agentops_sdk/security/dlp.py`.
- [x] **Hexagonal Architecture & Verification (`v1.0.0`)**: Separate pure contracts in `ports/` from 12 domain/adapter subpackages, enforce `mypy --strict`, `ruff` complexity/security checks, AST length budgets (`<=300` LOC/file, `<=40` LOC/func), hermetic `tests/unit/` + `tests/contract/`, and root `pyproject.toml` packaging (`agentops-sdk==1.0.0`).
