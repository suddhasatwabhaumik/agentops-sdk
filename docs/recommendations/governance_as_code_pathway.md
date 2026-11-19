<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# Technical Recommendation: Governance-as-Code (GoC) Golden Pathway

> [!NOTE]
> **Document Status — Proposed Platform Blueprint on `v1.0.0`**: The CI/CD quality gates (`--eval-mode`, `assert_quality_gate`), pinned OPA runner scaffold (`opa version`, ready for Rego `policies/`) in `cloudbuild.yaml`, `ExecutionScope`, `@trace_agent_span`, `@validate_tool_output`, `SecurityGuard`, `OutputDLPSanitizer`, and `AuditLedger` are **shipped in `v1.0.0`**. The unified `BaseEnterpriseAgent` wrapper class and Terraform CMEK pipeline templates below represent **proposed standardization extensions**.

This document outlines the recommended "Golden Pathway" to enforce AI security, tracing, quality, and cost compliance as codified rules across the lifecycle of all Google Customer agentic applications, in the absence of a central Skill Registry.

---

## 1. The Strategy: Automated Shift-Left and Shift-Right Enforcement

Governance-as-Code (GoC) replaces developer manual alignment with automated policy enforcement. The pathway utilizes three architectural checkpoints:

```
+------------------+     (Static Check)      +-------------------+
|  1. Development  |========================>| 2. CI/CD Pipeline |
|  - SDK Base Class|                         | - Quality Gates   |
|  - Auto-tracing  |                         | - Eval tasks      |
+------------------+                         +---------|---------+
                                                       | (Deploy)
                                                       v
+------------------+     (Active Inline)     +-------------------+
|  4. GCP Gateway  |<========================| 3. Runtime Image  |
|  - WAF Ingress   |                         | - Env Injections  |
|  - DLP Sanitizer |                         | - CMEK Enforced   |
+------------------+                         +-------------------+
```

---

## 2. Pathway Checkpoint 1: Dev-time Enforcements (SDK Level)

Instead of requiring developers to manually decorate functions with security or tracing wrappers, the SDK enforces policies via **Base Class Inheritance**.

### 2.1 The Proposed `BaseEnterpriseAgent` Class
Teams can standardize instrumentation on top of shipped `v1.0.0` primitives (`bind_execution_scope`, `@trace_agent_span`, `@validate_tool_output`) via a thin base runner class:

```python
from abc import ABC, abstractmethod
from agentops_sdk.domain.context import bind_execution_scope
from agentops_sdk.domain.models import TelemetryContext
from agentops_sdk.resilience import validate_tool_output
from agentops_sdk.telemetry import trace_agent_span


class BaseEnterpriseAgent(ABC):
    """Proposed standard base class for Google Customer custom agents on v1.0.0."""

    def __init__(self, agent_name: str, telemetry_context: TelemetryContext):
        self.agent_name = agent_name
        self.context = telemetry_context

    async def execute(self, user_query: str, session_id: str, user_id: str) -> str:
        # 1. Mount variables into async-safe ExecutionScope automatically
        with bind_execution_scope(session_id=session_id, user_id=user_id):
            # 2. Run execution wrapped with audit & OTel tracing
            return await self._instrumented_run(user_query)

    @abstractmethod
    async def run(self, query: str) -> str:
        """User agent logic runs here (overridden by developer)."""

    @trace_agent_span(span_name="base_enterprise_agent.run")
    @validate_tool_output(max_retries=1)
    async def _instrumented_run(self, query: str) -> str:
        # Delegates to subclass run() inside OTel span + validation boundary
        return await self.run(query)
```

*   **Golden Pathway**: Developers inherit from `BaseEnterpriseAgent` and implement `run()`. Telemetry, tracking, `ExecutionScope` context mapping, and output validation execute automatically.

---

## 3. Pathway Checkpoint 2: Build-time Gates (CI/CD Level)

Policies are checked and validated inside automated repository compilation runs before images are built (see `cloudbuild.yaml`, `.github/workflows/build-wheel.yml`, and `examples/cicd_templates/agent_quality_gates.yml`).

### 3.1 Static Quality Evals
CI/CD runs (GitHub Actions or Cloud Build) execute a mandatory quality gate against a Golden Dataset:
*   Executes the target agent over standard evaluation inputs (`pytest --eval-mode=in_memory` for offline PR checks; `vertex` for cloud pre-prod gates).
*   Calls an `EvaluatorPort` implementation (`VertexEvalTaskAdapter`, `RagasEvaluator`, `InMemoryEvaluator`, or `HeuristicEvaluator`) and `assert_quality_gate(...)` to verify **Groundedness** and **Response Quality** scores.
*   If the aggregate score falls below **0.80**, `QualityGateError` fails the CI step, blocking deployment.

### 3.2 Automated Dependency Scans
CI/CD steps run static code/policy scanners (`ruff`, `mypy --strict`, and a pinned `opa` runner scaffold executing `opa version` in `cloudbuild.yaml`, ready for Rego `policies/`):
*   Blocks unapproved direct database drivers and custom unvalidated network calls.
*   Ensures external queries route through approved authenticated MCP server gateways (`mcp_servers/src/server.py`).

---

## 4. Pathway Checkpoint 3: Deployment-time Enforcements (IaC Level)

Deployments are constrained at the resource level using Google Cloud policies and Terraform templates.

### 4.1 Custom-Managed Encryption (CMEK) Guardrails
Organisations require encryption at rest. Terraform configurations enforce CMEK globally:
*   Buckets (`google_storage_bucket`), databases (`google_sql_database_instance`), and container registries (`google_artifact_registry_repository`) fail to deploy unless a valid KMS Key resource (`kms_key_name`) is passed.

### 4.2 Standard Ingress Settings
All Cloud Run deployments for agent APIs default to `ingress = INGRESS_TRAFFIC_INTERNAL_ONLY`, blocking direct internet visibility.

---

## 5. Pathway Checkpoint 4: Runtime Enforcements (Gateway & MCP Layer)

The FastMCP Governance Gateway (`mcp_servers/src/server.py`) and optional API Gateway serve as the final active shield between conversational clients (Copilot Studio, Agentforce, ADK) and model/data runtimes:

1.  **PII Sanitization**: Sensitive tool and model outputs route through `OutputDLPSanitizer` (`GoogleDLPScrubber`, fail-closed), masking names, emails, and identifiers before return.
2.  **Model Armor Filtering**: Prompt inputs and responses are evaluated by `SecurityGuard` (`CloudModelArmorScanner`, `fail_closed=True`). If `verdict == SecurityVerdict.BLOCK`, execution terminates immediately.
3.  **Auditing Ledger**: Database and agent tool calls stream structured `AuditLogEntry` records via `AuditLedger` (`CloudLoggingAuditAdapter` default or `BigQueryAuditAdapter`) asynchronously via a bounded executor.
