<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# Remediate AgentOps Gateway Security and Capacity (`v1.0.0`)

This document summarizes the architectural and security remediations originally introduced during gateway hardening and now codified in `agentops-sdk` `v1.0.0` and `mcp_servers/src/`.

## High-Level Remediations

1. **Cryptographic JWT & Secret Enforcement (`CWE-798`, `CWE-287`, `CWE-347`)**
   *   **Initial Flaw**: Legacy configurations permitted hardcoded fallback JWT secrets (`DEFAULT_LOCAL_JWT_SECRET`) or test mock tokens, creating authentication bypass risk.
   *   **Current `v1.0.0` Enforcement**: `JWTAuthEngine` ([`sdk/src/agentops_sdk/security/auth.py`](../sdk/src/agentops_sdk/security/auth.py)) contains zero hardcoded fallback secrets. Outside explicit local dev/test environments (`ENVIRONMENT in {"dev", "development", "local", "test"}`), instantiating `JWTAuthEngine` without an SSRF-validated `jwks_url` or a `>= 32`-byte `JWT_SECRET_KEY` raises `ConfigurationError` (fail-closed). Similarly, `SecretManagerStore` ([`secrets.py`](../sdk/src/agentops_sdk/security/secrets.py)) fails closed with `SecretAccessError` rather than falling back to environment defaults, and `mcp_servers/src/auth_guard.py` enforces `MCP_REQUIRE_AUTH=true` by default without `MCP_AUTH_TOKEN` server impersonation or LLM-exposed `auth_token` parameters.

2. **Telemetry Batching & Metrics (`BatchSpanProcessor` + `MeterProvider`)**
   *   **Initial Flaw**: Legacy telemetry setup used `SimpleSpanProcessor` (blocking on export) without standardized `opentelemetry.metrics` instruments.
   *   **Current `v1.0.0` Enforcement**: `configure_telemetry(mode=...)` ([`sdk/src/agentops_sdk/telemetry/provider.py`](../sdk/src/agentops_sdk/telemetry/provider.py)) initializes `TracerProvider` with `BatchSpanProcessor` (for GCP Cloud Trace and OTLP), `MeterProvider` with `PeriodicExportingMetricReader` (`OTLPMetricExporter` / `CloudMonitoringMetricsExporter`), and `OtlpStdlibLogHandler` bridging, plus flat structured FinOps logs (`emit_finops_telemetry`).

3. **Logger Naming & Concurrency Isolation**
   *   **Initial Flaw**: Shared logger names caused duplicate log records, and unisolated locks risked cross-event-loop contention.
   *   **Current `v1.0.0` Enforcement**: Modules consistently initialize `logger = logging.getLogger(__name__)`, `AsyncRateLimiter` isolates `asyncio.Lock` per running event loop via `weakref.WeakKeyDictionary`, and `RateLimiterCore` guards `BucketState` under `threading.Lock` (`_state_lock`).

## E2E & Unit Testing
Automated regression scripts reside under `tests/e2e_remediate_gateway_security/`:
*   [`test_1_jwt_enforcement.sh`](../tests/e2e_remediate_gateway_security/test_1_jwt_enforcement.sh): Verifies production fail-closed `JWTAuthEngine`, access/refresh token type separation, `SecretAccessError` behavior, and `auth_guard.authenticate_caller` rejection of invalid tokens.
*   [`test_2_telemetry_batching.sh`](../tests/e2e_remediate_gateway_security/test_2_telemetry_batching.sh): Verifies `BatchSpanProcessor` registration and flat `finops_llm_telemetry` emission via `configure_telemetry(mode="otlp")`.

```bash
# Run gateway security & batching E2E suite
bash tests/e2e_remediate_gateway_security/run_all_remediate_gateway_security_tests.sh

# Run unit tests
pytest -v tests/unit/test_security_auth.py tests/unit/test_mcp_server.py tests/unit/test_telemetry.py --eval-mode=in_memory
```

## Key Configuration Variables

| Variable | Usage | Requirement |
| :--- | :--- | :--- |
| `AGENTOPS_IDENTITY__JWKS_URL` | Enterprise RS256 JWKS URL (`https://`, SSRF-guarded). | Recommended for production (`AGENTOPS_IDENTITY__ALLOWED_JWKS_HOSTS` recommended). |
| `JWT_SECRET_KEY` | Symmetric HS256 signing/verification key (`>= 32` UTF-8 bytes outside dev). | **Required** in non-dev envs if `AGENTOPS_IDENTITY__JWKS_URL` is not used. |
| `MCP_REQUIRE_AUTH` | Enforces mandatory Bearer JWT checks across MCP tools (`"true"` default). | Fail-closed `"true"` in production. |
