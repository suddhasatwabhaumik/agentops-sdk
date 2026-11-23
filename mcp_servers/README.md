<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# Model Context Protocol (MCP) Server - Developer Reference (`v1.0.0`)

`mcp_servers/` provides the hardened **FastMCP governance gateway** for exposing governed SQL (BigQuery / Snowflake / Databricks SQL Warehouse) and Vertex AI Feature Online Store lookups as MCP tools over `stdio`, `sse`, or `streamable-http`.

All SQL and Feature Store calls pass through JWT authentication, boundary-anchored SQL AST validation (`agentops_sdk.sql.SQLASTValidator`), Row-Level Security (`RowLevelPolicyEngine`), and tenant-scoped entity allowlisting before dispatching to `CONNECTOR_API_URL`.

---

## 1. Architecture & Request Flow

```
+-------------------------------------------------------------------------------+
|                    ZERO-TRUST MCP GOVERNANCE GATEWAY FLOW                     |
+-------------------------------------------------------------------------------+
|                                                                               |
|  [LLM Agent: Google ADK / LangGraph / MCP Client]                             |
|        |                                                                      |
|        | 1. Invokes MCP tool (Authorization: Bearer <JWT> HTTP header         |
|        |    or active agentops_sdk ExecutionScope)                            |
|        v                                                                      |
|  [ASGI HeaderBindingMiddleware + auth_guard (mcp_servers/src/)]               |
|        |                                                                      |
|        | 2. Verifies JWT (JWTAuthEngine), validates SQL AST & injects RLS,    |
|        |    or enforces Feature Store tenant & entity scope                   |
|        v                                                                      |
|  [HTTPS Upstream Endpoint (${CONNECTOR_API_URL}/v1/query)]                    |
|        |                                                                      |
|        | 3. Executes governed query on BigQuery / Databricks / Feature Store  |
|        v                                                                      |
|                                                                               |
+-------------------------------------------------------------------------------+
```

---

## 2. Directory Structure

```
mcp_servers/
├── Dockerfile             # Repo-root multi-layer build (non-root user mcpuser)
├── requirements.txt       # Standalone container dependencies (mcp[cli], httpx)
├── README.md              # Developer reference & Cloud Run deployment guide
└── src/
    ├── __init__.py
    ├── _http.py           # HeaderBindingMiddleware, CONNECTOR_API_URL TLS check, bind config
    ├── auth_guard.py      # Caller JWT auth, SQL AST + RLS guard, Feature Store tenant scope
    └── server.py          # FastMCP tools & CLI entrypoint (`python -m mcp_servers.src.server`)
```

---

## 3. Security Model & Tool Reference (`v1.0.0`)

> [!IMPORTANT]
> **No `auth_token` tool parameter**: Tool schemas intentionally omit `auth_token` so LLM prompts cannot override or spoof caller credentials. Caller JWTs are resolved strictly from HTTP `Authorization: Bearer <token>` headers (captured by `HeaderBindingMiddleware` or FastMCP request context) or `agentops_sdk.domain.context.bind_execution_scope(identity_token=...)`.

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `MCP_REQUIRE_AUTH` | `"true"` | Fail-closed JWT verification on every tool call (set `"false"` only for trusted offline tests) |
| `MCP_ALLOWED_TABLES` | `""` | Comma-separated SQL table allowlist (e.g. `analytics.inventory,analytics_eu.*`) |
| `MCP_ALLOWED_ENTITY_TYPES` | `""` | Comma-separated allowlist of Vertex Feature Store `entity_type` values |
| `CONNECTOR_API_URL` | `"http://127.0.0.1:8081"` | Upstream connector base URL (`https://` required for non-loopback hosts unless `ALLOW_INSECURE_CONNECTOR_HTTP=true`) |
| `MCP_TRANSPORT` | `"stdio"` | Default CLI transport (`stdio`, `sse`, or `streamable-http`) |
| `MCP_HOST` | `"127.0.0.1"` | HTTP bind host when running `sse` or `streamable-http` |
| `PORT` | `"8080"` | HTTP bind port when running `sse` or `streamable-http` |
| `JWT_SECRET_KEY` / `AGENTOPS_IDENTITY__JWKS_URL` | `None` | JWT verification config forwarded to `JWTAuthEngine` (`AGENTOPS_IDENTITY__ALLOWED_JWKS_HOSTS` recommended for JWKS) |
| `AGENTOPS_IDENTITY__CLIENT_ID` / `AGENTOPS_IDENTITY__ISSUER` | `None` | Expected JWT `aud` and `iss` claims enforced by `JWTAuthEngine` inside `auth_guard.py` |
| `ENVIRONMENT` | `"development"` | Runtime mode (`dev \| development \| local \| test` vs production fail-closed JWT enforcement) |

### Exposed Tools

1. **`query_database_tool(query_template, parameters=None)`**
   *   Enforces caller JWT auth (`MCP_REQUIRE_AUTH=true`), BigQuery-dialect `SQLASTValidator` (`MCP_ALLOWED_TABLES`), and `RowLevelPolicyEngine` predicate injection before posting to `${CONNECTOR_API_URL}/v1/query`.
2. **`query_databricks_tool(query_template, parameters=None)`**
   *   Enforces caller JWT auth, Databricks-dialect SQL AST validation, and RLS predicate injection before posting to `${CONNECTOR_API_URL}/v1/query`.
3. **`query_feature_store_tool(entity_type, entity_ids, features)`**
   *   Enforces caller JWT auth, validates `entity_type` against `MCP_ALLOWED_ENTITY_TYPES` and caller claims (`allowed_entity_types`, `allowed_entity_ids`, `tenant_id:` prefix matching), and attaches verified `tenant_id` in upstream query parameters.

---

## 4. Agent Framework Integration

### 4.1 Google ADK (`mcpServers` StdIO Config)
```json
{
  "mcpServers": {
    "db-connector-mcp": {
      "command": "python3",
      "args": ["-m", "mcp_servers.src.server", "--transport", "stdio"],
      "env": {
        "CONNECTOR_API_URL": "https://your-connector.europe-west4.run.app",
        "MCP_ALLOWED_TABLES": "analytics.tenant_inventory,analytics.parts",
        "MCP_ALLOWED_ENTITY_TYPES": "tenant,inventory_sku"
      }
    }
  }
}
```

### 4.2 SSE / Streamable HTTP Client
When connecting over HTTP (`sse` or `streamable-http`), send `Authorization: Bearer <JWT>` in HTTP request headers:

```python
import os

from mcp import ClientSession
from mcp.client.sse import sse_client

SERVER_SSE_URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8080/sse")
ACCESS_TOKEN = os.environ["ACCESS_TOKEN"]


async def list_governed_tools():
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    async with sse_client(SERVER_SSE_URL, headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.list_tools()
```

---

## 5. Local Development & Debugging

Run commands from the **repository root** (`pip install -e ".[all]"`):

```bash
# Run in default stdio mode
CONNECTOR_API_URL="http://127.0.0.1:8081" \
  python3 -m mcp_servers.src.server --transport stdio

# Run in SSE mode (ASGI HeaderBindingMiddleware active)
CONNECTOR_API_URL="http://127.0.0.1:8081" \
  python3 -m mcp_servers.src.server --transport sse --host 127.0.0.1 --port 8080

# Inspect tools interactively with MCP Inspector
npx -y @modelcontextprotocol/inspector \
  python3 -m mcp_servers.src.server --transport stdio
```

---

## 6. Container Build & Cloud Run Deployment

> [!IMPORTANT]
> **Build context must be the repository root** because [`mcp_servers/Dockerfile`](Dockerfile) copies root [`pyproject.toml`](../pyproject.toml) and [`sdk/`](../sdk/) to install `agentops-sdk[mcp]` cleanly before copying `mcp_servers/`.

> [!NOTE]
> **Customer Placeholders**: Replace `${PROJECT_ID}`, `europe-west4`, `agentops`, `agentops-mcp-server`, `analytics.*`, and connector URLs with your environment settings as documented in [Customer Environment & Placeholder Configuration](../README.md#customer-environment--placeholder-configuration). Root [`cloudbuild.yaml`](../cloudbuild.yaml) runs lint/typecheck/tests and builds/publishes the Python SDK wheel (`agentops-python-repo`); build and push the MCP container image using Docker + Artifact Registry below.

```bash
# 1. Local Docker build from the repository root
docker build -f mcp_servers/Dockerfile \
  -t europe-west4-docker.pkg.dev/${PROJECT_ID}/agentops/agentops-mcp-server:v1.0.0 .

# 2. Authenticate Docker to Artifact Registry and push the image
gcloud auth configure-docker europe-west4-docker.pkg.dev
docker push europe-west4-docker.pkg.dev/${PROJECT_ID}/agentops/agentops-mcp-server:v1.0.0

# 3. Deploy non-root SSE container to Cloud Run (HTTPS upstream required)
gcloud run deploy agentops-mcp-server \
  --image europe-west4-docker.pkg.dev/${PROJECT_ID}/agentops/agentops-mcp-server:v1.0.0 \
  --region europe-west4 \
  --vpc-egress all-traffic \
  --network default \
  --subnet private-sub \
  --no-allow-unauthenticated \
  --set-env-vars CONNECTOR_API_URL="https://connector-internal.europe-west4.run.app",MCP_REQUIRE_AUTH="true"
```
