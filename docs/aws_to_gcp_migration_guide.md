<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# AWS to GCP Agentic Application Migration Guide (Enterprise Agentic Application, `v1.0.0`)

This document is a technical blueprint to guide Google Customer developers through migrating legacy AWS-based **Enterprise Agentic Applications** to Google Cloud Platform (GCP) using the **Google Agent Development Kit (ADK)**, **Vertex AI Agent Engine**, and `agentops-sdk` (`v1.0.0`).

---

## 1. Migration Architecture Map

The legacy AWS components map directly to native GCP and `agentops-sdk` `v1.0.0` equivalents:

| Legacy AWS Component | GCP / Google ADK Equivalent | Migration Strategy |
| :--- | :--- | :--- |
| **Amazon EC2 (LangGraph Host)** | **Vertex AI Agent Engine / Cloud Run** | Deploy agent class wrapper natively to Vertex AI Agent Engine or serverless Cloud Run. |
| **Amazon ECS Sandboxes (DataFrame RAM)** | **Vertex AI Code Execution / BigFrames** | Execute analytical code in managed GCP runtimes; persist results database-side. |
| **Amazon S3 / Redshift** | **GCS + BigQuery / BigLake + MCP Gateway** | Leverage BigQuery/BigLake Native Delta or the hardened MCP Gateway (`mcp_servers/src/server.py`) to execute governed analytical queries. |
| **PostgreSQL Database** | **Cloud SQL for PostgreSQL** | Connect via `CloudSQLSessionMemory` (`agentops-sdk[cloudsql]`) for persistent relational session turns. |
| **Zilliz Cloud (Milvus)** | **Vertex AI Memory Bank / Vector Search** | Persist cross-session facts in `VertexMemoryBank` (`scope={"user_id": ...}`) or Vertex AI Vector Search within the GCP VPC boundary. |
| **Langfuse Monitoring** | **Cloud Trace, Cloud Monitoring & Cloud Logging** | Instrument multi-agent spans and FinOps telemetry via `configure_telemetry()`, `@trace_agent_flow`, and `@trace_llm_call`. |

---

## 2. Step-by-Step Migration Guide

### Step 1: Migrate Session Memory to Cloud SQL PostgreSQL
Install `agentops-sdk[cloudsql]` and store structured conversational turns in Cloud SQL PostgreSQL via `CloudSQLSessionMemory`:

```python
from agentops_sdk.memory import CloudSQLSessionMemory

# Initialize database-backed session memory (explicit connection_url required; no SQLite fallback)
session_memory = CloudSQLSessionMemory(
    connection_url="postgresql+psycopg2://user:password@cloud-sql-ip:5432/agent_sessions"
)

# Store turn state
session_memory.add_message(
    session_id="session-user-789",
    role="assistant",
    content="Parsed operational KPI metrics for Plant-SA-01.",
    metadata={"kpi_score": 1.25},
)
```

### Step 2: Implement Enterprise Agent Class with `agentops_sdk` `v1.0.0`
Wrap your orchestrator logic inside a class using the canonical subpackages (`domain`, `prompts`, `security`, `telemetry`):

```python
import os

from agentops_sdk.domain.context import bind_execution_scope
from agentops_sdk.domain.models import TelemetryContext
from agentops_sdk.prompts import PromptRegistry
from agentops_sdk.security import CloudModelArmorScanner, SecurityGuard
from agentops_sdk.telemetry import (
    configure_telemetry,
    trace_agent_flow,
    trace_llm_call,
)

# Define business telemetry context for trace and FinOps attribution
telemetry_context = TelemetryContext(
    business_kpi="kpi-target-reduction",
    pl_impact_line="operational-efficiency-opex",
    project_name="enterprise-advisor-agent",
    environment="production",
)


class EnterpriseAgent:
    def __init__(self, project: str, location: str = "europe-west4") -> None:
        self.project = project
        self.location = location
        self.registry: PromptRegistry | None = None
        self.guard: SecurityGuard | None = None
        self.client = None

    def set_up(self) -> None:
        """Executed upon container startup to initialize telemetry and clients."""
        from google.genai import Client

        configure_telemetry(
            mode="gcp",
            project_id=self.project,
            service_name="enterprise-advisor-agent",
        )
        self.registry = PromptRegistry(
            backend="local",
            local_dir="prompts",
        )
        self.guard = SecurityGuard(
            scanner=CloudModelArmorScanner(
                project_id=self.project,
                location=self.location,
            ),
            fail_closed=True,
        )
        self.client = Client(
            vertexai=True,
            project=self.project,
            location=self.location,
        )

    @trace_agent_flow(context=telemetry_context, agent_role="enterprise_advisor")
    def query(self, user_prompt: str, session_id: str, caller_jwt: str) -> str:
        """Endpoint invoked for each conversational turn."""
        if self.guard is None or self.registry is None:
            self.set_up()
        assert self.guard is not None and self.registry is not None

        with bind_execution_scope(
            session_id=session_id,
            identity_token=caller_jwt,
        ):
            # 1. Fail-closed safety check (prompt injection & PII)
            decision = self.guard.check_input(user_prompt)
            if not decision.allowed:
                return f"Blocked: policy violation ({decision.violation_category})."

            sanitized_input = decision.sanitized_text

            # 2. Retrieve session message history
            history = session_memory.get_messages(session_id, limit=20)

            # 3. Query site metrics via governed MCP tool (reads ExecutionScope JWT)
            db_context = fetch_site_metrics_tool(
                site_location="Plant-SA-01",
                year=2024,
            )

            # 4. Render prompt template in bounded Jinja2 SandboxedEnvironment
            final_prompt = self.registry.render_prompt(
                "enterprise_analytics_agent",
                {
                    "query": sanitized_input,
                    "context": db_context,
                    "history": str([turn.content for turn in history]),
                },
            )

            # 5. Invoke Gemini (wrapped in @trace_llm_call for token FinOps telemetry)
            response_text = self._call_inference(final_prompt)

            # 6. Persist conversational turn state
            session_memory.add_message(session_id, "user", sanitized_input)
            session_memory.add_message(session_id, "assistant", response_text)
            return response_text

    @trace_llm_call(context=telemetry_context)
    def _call_inference(self, prompt: str) -> str:
        """Executes inference call via google.genai.Client."""
        assert self.client is not None
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        response = self.client.models.generate_content(
            model=model,
            contents=prompt,
        )
        return response.text or ""
```

### Step 3: Invoke Governed Databricks / BigQuery MCP Tools
Bind caller identity via `ExecutionScope` (`bind_execution_scope(identity_token=...)` or `@pass_user_identity`) instead of passing raw tokens in LLM-facing tool schemas, and guard tool outputs with `@validate_tool_output`:

```python
from agentops_sdk.resilience import validate_tool_output
from agentops_sdk.security import pass_user_identity
from mcp_servers.src.server import query_databricks_tool


@pass_user_identity()
@validate_tool_output(max_retries=3)
def fetch_site_metrics_tool(site_location: str, year: int) -> str:
    """Retrieves operational KPI metrics for the given site and year via MCP Gateway."""
    sql = """
        SELECT kpi_score, review_meetings_count
        FROM analytics.site_metrics
        WHERE site_name = :site AND year_val = :year
    """
    params = {"site": site_location, "year": year}
    return query_databricks_tool(query_template=sql, parameters=params)
```

### Step 4: Deploy to Vertex AI Agent Engine
Package `agentops-sdk[cloudsql,mcp]` (or `agentops-sdk[all]`) cleanly from PyPI / Artifact Registry / Git tag (directly from the repository root):

```python
import vertexai

client = vertexai.Client(project="<your-gcp-project-id>", location="europe-west4")
remote_agent = client.agent_engines.create(
    agent=EnterpriseAgent(project="<your-gcp-project-id>", location="europe-west4"),
    config={
        "requirements": ["agentops-sdk[cloudsql,mcp]==1.0.0"],
        "display_name": "enterprise-advisor-agent",
    },
)
```

---

## 3. Deployment & CI/CD

Automate quality verification and packaging using the repository's root pipelines:
*   **GitHub Actions Quality Gate Template**: [`examples/cicd_templates/agent_quality_gates.yml`](../examples/cicd_templates/agent_quality_gates.yml) runs fast `--eval-mode=in_memory` gates on Pull Requests and `--eval-mode=vertex` gates on main/nightly runs.
*   **Google Cloud Build Pipeline**: [`cloudbuild.yaml`](../cloudbuild.yaml) runs `ruff check`, `ruff format --check`, `mypy --strict`, pinned OPA policy checks, `pytest -v tests/unit tests/contract --eval-mode=in_memory`, and wheel packaging via `python -m build .`.
