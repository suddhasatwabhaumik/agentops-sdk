<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# Model Context Protocol (MCP) Integration & Implementation (`v1.0.0`)

This guide explains how to configure, secure, test, and consume the **Model Context Protocol (MCP) Governance Gateway** in [`mcp_servers/`](../mcp_servers/) alongside `agentops_sdk` `v1.0.0`.

---

## 1. What is Model Context Protocol (MCP)?

The Model Context Protocol (MCP) standardizes how AI agents (clients) discover and invoke external tools, databases, and feature stores (servers):
*   **Clients** (e.g., Google ADK, LangGraph, MCP Inspector) connect over `stdio`, `sse`, or `streamable-http` to discover registered tool schemas.
*   **Servers** (`mcp_servers/src/server.py`) expose governed tools backed by `JWTAuthEngine`, `SQLASTValidator`, `RowLevelPolicyEngine`, and tenant-scoped Feature Store checks before forwarding validated requests to `${CONNECTOR_API_URL}/v1/query`.

---

## 2. Hardened Gateway Implementation (`mcp_servers/src/`)

### Code Layout
*   [`mcp_servers/src/server.py`](../mcp_servers/src/server.py): FastMCP server registering `query_database_tool`, `query_databricks_tool`, and `query_feature_store_tool`, plus CLI entrypoint `python -m mcp_servers.src.server`.
*   [`mcp_servers/src/auth_guard.py`](../mcp_servers/src/auth_guard.py): JWT verification (`authenticate_caller`), auth-before-AST ordering (`authorize_query`), SQL AST + RLS checks (`validate_and_authorize_sql`), and Feature Store tenant authorization (`authorize_feature_store_tenant`).
*   [`mcp_servers/src/_http.py`](../mcp_servers/src/_http.py): ASGI `HeaderBindingMiddleware`, `CONNECTOR_API_URL` HTTPS enforcement (`get_connector_api_url`), and bind config validation (`resolve_bind_config`).
*   [`mcp_servers/Dockerfile`](../mcp_servers/Dockerfile): Repo-root non-root (`mcpuser`) container build.

### Zero-Trust Security Controls (`v1.0.0`)

> [!IMPORTANT]
> **No `auth_token` Parameter on Tool Schemas (`CWE-288`)**: None of the exposed MCP tools accept an `auth_token` argument. Removing `auth_token` prevents LLM prompts from overriding or leaking caller tokens. All `MCP_AUTH_TOKEN` server fallbacks are likewise removed.

*   **Fail-Closed Auth (`MCP_REQUIRE_AUTH=true` default, `CWE-306`)**: Every tool call requires a valid caller `access` JWT resolved from HTTP `Authorization: Bearer <token>` headers (bound automatically by ASGI `HeaderBindingMiddleware` or FastMCP request context) or `bind_execution_scope(identity_token=...)`.
*   **Pre-Auth Oracle Prevention (`CWE-203`)**: `authorize_query()` authenticates the caller JWT *before* invoking `SQLASTValidator` (`MCP_ALLOWED_TABLES`), preventing unauthenticated table-existence enumeration.
*   **SQL AST & RLS Enforcement (`CWE-89`)**: `query_database_tool` (`dialect="bigquery"`) and `query_databricks_tool` (`dialect="databricks"`) enforce read-only `SELECT` AST validation and inject tenant Row-Level Security (`RowLevelPolicyEngine`).
*   **Feature Store Tenant Isolation (`CWE-284`)**: `query_feature_store_tool(entity_type, entity_ids, features)` enforces `MCP_ALLOWED_ENTITY_TYPES`, JWT `tenant_id`/`tid` presence (unless `role in {"admin", "superadmin"}`), claim-level `allowed_entity_types` / `allowed_entity_ids`, and cross-tenant `tenant:entity_id` prefix checks.
*   **Upstream HTTPS Enforcement (`CWE-319`)**: `CONNECTOR_API_URL` defaults to `http://127.0.0.1:8081`. Any non-loopback `CONNECTOR_API_URL` requires `https://` unless `ALLOW_INSECURE_CONNECTOR_HTTP=true` is set explicitly.

---

## 3. Remote Client Integration (SSE / Streamable HTTP Transport)

When connecting over HTTP (`sse` or `streamable-http`), pass the caller's JWT via the HTTP `Authorization` header (captured automatically by `HeaderBindingMiddleware`):

```python
import asyncio
import os

from mcp import ClientSession
from mcp.client.sse import sse_client


async def execute_query_on_remote_mcp(sql_query: str, bearer_token: str) -> str:
    """Connects over SSE with Authorization header and invokes query_database_tool."""
    server_url = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8080/sse")
    headers = {"Authorization": f"Bearer {bearer_token}"}

    async with sse_client(server_url, headers=headers) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            response = await session.call_tool(
                name="query_database_tool",
                arguments={
                    "query_template": sql_query,
                    "parameters": {"site": "Plant-EU-01"},
                },
            )
            return response.content[0].text


def run_query(sql_query: str, bearer_token: str) -> str:
    return asyncio.run(execute_query_on_remote_mcp(sql_query, bearer_token))
```

---

## 4. Local Testing, Docker Build & Debugging

### Step 1: Install from Repository Root
```bash
pip install -e ".[dev,all]"
# Or strictly MCP + core SDK deps:
pip install -e ".[mcp]"
```

### Step 2: Run the MCP Server (`stdio` or `sse`)
Run using module mode from the **repository root**:
```bash
# Default stdio mode
CONNECTOR_API_URL="http://127.0.0.1:8081" \
MCP_ALLOWED_TABLES="analytics.metrics,analytics.parts" \
python3 -m mcp_servers.src.server --transport stdio

# SSE mode with ASGI HeaderBindingMiddleware active
CONNECTOR_API_URL="http://127.0.0.1:8081" \
MCP_REQUIRE_AUTH="true" \
python3 -m mcp_servers.src.server --transport sse --host 127.0.0.1 --port 8080
```

### Step 3: Validate Interactively with MCP Inspector
```bash
npx -y @modelcontextprotocol/inspector \
  python3 -m mcp_servers.src.server --transport stdio
```

### Step 4: Container Build (Repository Root Context)
Build from the repository root because [`mcp_servers/Dockerfile`](../mcp_servers/Dockerfile) copies root `pyproject.toml` and `sdk/`:
```bash
docker build -f mcp_servers/Dockerfile \
  -t europe-west4-docker.pkg.dev/${PROJECT_ID}/agentops/agentops-mcp-server:v1.0.0 .
```

### Step 5: Run Unit Tests
```bash
pytest -v tests/unit/test_mcp_server.py --eval-mode=in_memory
```

---

## 5. Using `agentops_sdk` Safety, Memory & Prompts with MCP Tools

Agents calling MCP tools in-process bind caller identity into `ExecutionScope` via `bind_execution_scope(identity_token=...)` rather than passing tokens as LLM tool arguments:

```python
import logging

from agentops_sdk.domain.context import bind_execution_scope
from agentops_sdk.memory import InMemorySessionMemory
from agentops_sdk.prompts import InMemoryPromptAdapter, PromptRegistry
from agentops_sdk.security import LocalRuleSafetyScanner, SecurityGuard
from mcp_servers.src.server import query_databricks_tool

logger = logging.getLogger("enterprise-agent")

# Inject production adapters (CloudModelArmorScanner, CloudSQLSessionMemory,
# VertexAIPromptAdapter) in GCP deployments; InMemory/Local adapters shown here.
guard = SecurityGuard(scanner=LocalRuleSafetyScanner(), fail_closed=True)
registry = PromptRegistry(adapter=InMemoryPromptAdapter())
registry.register_prompt(
    prompt_id="analytics_query_template",
    version="1.0.0",
    template="User question: {{ query }}\nContext: {{ context }}",
)
session_memory = InMemorySessionMemory()


def handle_agent_query(
    user_prompt: str,
    session_id: str,
    caller_jwt: str,
) -> str:
    # 1. Fail-closed safety scan
    decision = guard.check_input(user_prompt)
    if not decision.allowed:
        logger.warning("Safety violation blocked query: %s", decision.violation_category)
        return f"Safety Warning: Blocked ({decision.violation_category})."

    # 2. Bind caller JWT in ExecutionScope (never passed as an LLM tool arg)
    sql = "SELECT kpi_score FROM analytics.metrics WHERE site = :site AND year = :year"
    params = {"site": "Plant-SA-01", "year": 2024}
    with bind_execution_scope(session_id=session_id, identity_token=caller_jwt):
        db_context = query_databricks_tool(query_template=sql, parameters=params)

    # 3. Render prompt in bounded Jinja2 SandboxedEnvironment
    rendered_prompt = registry.render_prompt(
        "analytics_query_template",
        {"query": decision.sanitized_text, "context": db_context},
    )

    # 4. Record turn state in session memory
    session_memory.add_message(session_id, "user", decision.sanitized_text)
    session_memory.add_message(session_id, "assistant", rendered_prompt)
    return rendered_prompt
```
