<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# Copilot Studio & API Gateway Wrapper Design (`v1.0.0`)

This document describes the architectural pattern, request/response contracts, private cross-cloud networking, and reference FastAPI integration for exposing `agentops_sdk` (`v1.0.0`) governance controls to low-code platforms such as **Microsoft Copilot Studio** and **Salesforce Agentforce**.

> [!NOTE]
> **Architecture Note (`v1.0.0`)**: Governed data and Feature Store access is provided directly by the **FastMCP Governance Gateway** ([`mcp_servers/src/server.py`](../mcp_servers/src/server.py)). Teams exposing REST endpoints to low-code connectors build a lightweight FastAPI wrapper (e.g., on Cloud Run) around the `agentops_sdk` `v1.0.0` classes documented below.

---

## 1. Gateway Architecture & Recommended REST Endpoints

Low-code platforms that call HTTP/OpenAPI actions rather than Python SDKs directly can invoke a thin FastAPI wrapper deployed on internal Cloud Run:

| Endpoint | Method | Input Model | Output Model | Backing `agentops_sdk` (`v1.0.0`) Component |
| :--- | :--- | :--- | :--- | :--- |
| `/v1/safety/check` | `POST` | `SafetyCheckRequest` | `SafetyCheckResponse` | `SecurityGuard(scanner=CloudModelArmorScanner(), fail_closed=True).check_input(...)` |
| `/v1/privacy/scrub` | `POST` | `PrivacyScrubRequest` | `PrivacyScrubResponse` | `OutputDLPSanitizer(scrubber=GoogleDLPScrubber(...))` |
| `/v1/audit/log` | `POST` | `AuditLogEntry` | `{"status": "logged"}` | `AuditLedger(sink=BigQueryAuditAdapter(...))` (or default `CloudLoggingAuditAdapter`) |
| `/v1/prompt/load` | `POST` | `PromptLoadRequest` | `PromptLoadResponse` | `PromptRegistry(backend="vertex_ai", ...).render_prompt(...)` |
| `/v1/memory/{session_id}` | `GET` | *None* | `MemoryGetResponse` | `CloudSQLSessionMemory.aget_messages(session_id, limit=50)` |
| `/v1/memory/{session_id}/message` | `POST` | `MemoryAddRequest` | `{"status": "added"}` | `CloudSQLSessionMemory.aadd_message(...)` |
| `/v1/memory/{session_id}` | `DELETE` | *None* | `{"status": "cleared"}` | `CloudSQLSessionMemory.aclear_session(session_id)` |

---

## 2. Reference Request/Response Contracts & FastAPI Wrapper

All gateway endpoints must verify caller Bearer tokens via `JWTAuthEngine` (`agentops_sdk.security.auth`) before invoking backend services:

```python
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from agentops_sdk.audit import AuditLedger
from agentops_sdk.domain.exceptions import AuthenticationError
from agentops_sdk.domain.models import AuditLogEntry, SecurityVerdict
from agentops_sdk.security import (
    JWTAuthEngine,
    OutputDLPSanitizer,
    RegexPrivacyScrubber,
    SecurityGuard,
    LocalRuleSafetyScanner,
)

app = FastAPI(title="AgentOps Low-Code Gateway")

# Production: omit custom scanner/scrubber to use CloudModelArmorScanner & GoogleDLPScrubber,
# and configure JWTAuthEngine(jwks_url=..., audience=..., issuer=...).
jwt_engine = JWTAuthEngine()
guard = SecurityGuard(scanner=LocalRuleSafetyScanner(), fail_closed=True)
scrubber = RegexPrivacyScrubber()
sanitizer = OutputDLPSanitizer(scrubber=scrubber)
ledger = AuditLedger()  # Defaults to CloudLoggingAuditAdapter (ADR-2)


def require_bearer_claims(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Bearer token.",
        )
    try:
        return jwt_engine.decode_token(authorization, expected_type="access")
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc


class SafetyCheckRequest(BaseModel):
    prompt: str


class SafetyCheckResponse(BaseModel):
    allowed: bool
    verdict: SecurityVerdict
    sanitized_text: str
    violation_category: str | None = None


class PrivacyScrubRequest(BaseModel):
    text: str


class PrivacyScrubResponse(BaseModel):
    sanitized_text: str
    pii_scrubbed: bool


class PromptLoadRequest(BaseModel):
    prompt_id: str
    variables: dict[str, Any] = Field(default_factory=dict)
    version: str = "latest"


@app.post("/v1/safety/check", response_model=SafetyCheckResponse)
def safety_check_route(
    body: SafetyCheckRequest,
    _claims: dict[str, Any] = Depends(require_bearer_claims),
) -> SafetyCheckResponse:
    decision = guard.check_input(body.prompt)
    return SafetyCheckResponse(
        allowed=decision.allowed,
        verdict=decision.verdict,
        sanitized_text=decision.sanitized_text,
        violation_category=decision.violation_category,
    )


@app.post("/v1/privacy/scrub", response_model=PrivacyScrubResponse)
async def privacy_scrub_route(
    body: PrivacyScrubRequest,
    _claims: dict[str, Any] = Depends(require_bearer_claims),
) -> PrivacyScrubResponse:
    scrubbed = await sanitizer.scrub_text(body.text)
    return PrivacyScrubResponse(
        sanitized_text=scrubbed,
        pii_scrubbed=scrubbed != body.text,
    )


@app.post("/v1/audit/log")
async def audit_log_route(
    entry: AuditLogEntry,
    _claims: dict[str, Any] = Depends(require_bearer_claims),
) -> dict[str, str]:
    await ledger.log_entry(entry)
    return {"status": "logged"}
```

---

## 3. Hybrid Networking & Dedicated Private Interconnect

Connecting Microsoft Copilot Studio (Azure SaaS) with GCP-hosted Cloud Run services uses private cross-cloud routing without public internet ingress:

```text
+------------------------------+             +-----------------------------------+
|     Microsoft Azure Tenant   |             |       Google Cloud Platform       |
|  +------------------------+  |   Private   |  +-----------------------------+  |
|  |     Copilot Studio     |==|====Link====>|   Partner Interconnect Gateway  |  |
|  |     Custom Connector   |  | (Express    |   (No Public IP Exposure)      |  |
|  +-----------|------------+  |  Route)     |  +--------------|--------------+  |
|              v               |             |                 v                 |
|  +------------------------+  |             |  +-----------------------------+  |
|  |  ExpressRoute Gateway  |  |             |  | Internal Load Balancer (ILB)|  |
|  +------------------------+  |             |  +--------------|--------------+  |
+------------------------------+             |                 v                 |
                                             |  +-----------------------------+  |
                                             |  |   Cloud Run (API Gateway)   |  |
                                             |  +--------------|--------------+  |
                                             |                 | Private VPC     |
                                             |  +--------------v--------------+  |
                                             |  | BigQuery / Cloud SQL / MCP  |  |
                                             |  +-----------------------------+  |
                                             +-----------------------------------+
```

### Ingress & Egress Perimeter
1.  **Cross-Cloud Interconnect**: Traffic traverses Azure ExpressRoute + GCP Partner Interconnect (or HA Cloud VPN) into an Internal Application Load Balancer (ILB). Cloud Run is configured with `--ingress=internal-and-cloud-load-balancing`.
2.  **JWT Verification & RLS**: The Gateway validates Entra ID RS256 tokens via `JWTAuthEngine` + `SafeJWKClient` (`validate_jwks_url` SSRF protection) and enforces `RowLevelPolicyEngine` before querying downstream data sources.
3.  **Private Google Access & Direct VPC Egress**: Calls to Cloud SQL, BigQuery, Model Armor, Cloud DLP, and Vertex AI route through Private Google Access / Serverless VPC Access without public internet transit.

---

## 4. Verification Testing

Wrapper gateways built around `agentops_sdk` can be verified using FastAPI's `TestClient` with explicit in-memory / local adapters (`LocalRuleSafetyScanner`, `RegexPrivacyScrubber`, `InMemoryAuditAdapter`, `InMemorySessionMemory`, `InMemoryPromptAdapter`). Within this repository, run the underlying SDK and MCP gateway unit suites:

```bash
pytest -v \
  tests/unit/test_security_guards.py \
  tests/unit/test_security_auth.py \
  tests/unit/test_audit.py \
  tests/unit/test_memory.py \
  tests/unit/test_prompts.py \
  tests/unit/test_mcp_server.py \
  --eval-mode=in_memory
```
