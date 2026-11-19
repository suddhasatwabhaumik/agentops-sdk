<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# Security, Identity & Verification Plan (`v1.0.0`)

**Version:** 1.0.0 (Hexagonal Architecture & 23-CWE Hardening)  
**Status:** Active Production Standard  
**Branch:** `main`  

This document specifies the zero-trust authentication controls, SSRF defenses, secret management, AST-level SQL governance, Model Armor / Cloud DLP guardrails, MCP gateway isolation, and security verification suites in `agentops-sdk` `v1.0.0` and `mcp_servers/src/`.

---

## 1. Zero-Trust Cryptographic Authentication (`agentops_sdk.security.auth`, `_jwks.py`, `identity.py`)

### 1.1 Asymmetric RS256 JWKS Verification & SSRF Guard (`SafeJWKClient`)
Corporate agents and gateway clients authenticate using Microsoft Entra ID (or OIDC) `RS256` JWTs verified via `JWTAuthEngine` ([`sdk/src/agentops_sdk/security/auth.py`](../sdk/src/agentops_sdk/security/auth.py)) and `SafeJWKClient` ([`sdk/src/agentops_sdk/security/_jwks.py`](../sdk/src/agentops_sdk/security/_jwks.py)):

*   **SSRF-Hardened URL Validation (`validate_jwks_url()`, `CWE-918`)**:
    *   Enforces `https://` scheme with non-empty hostname and rejects embedded URL `username`/`password` userinfo.
    *   Rejects `localhost`, `*.localhost`, `*.internal`, non-`is_global` IPs, loopback, RFC 1918 private, link-local (`169.254.0.0/16`), CGNAT (`100.64.0.0/10`), multicast/reserved/unspecified addresses, and IPv4-mapped / 6to4 / Teredo IPv6 addresses.
    *   Optionally enforces an explicit hostname allowlist via `allowed_jwks_hosts` or `AGENTOPS_IDENTITY__ALLOWED_JWKS_HOSTS`.
    *   Re-validates DNS resolution inside `SafeJWKClient.fetch_data()` before opening HTTPS connections (mitigating DNS rebinding) and blocks all HTTP `3xx` redirects via `_NoRedirectHandler`.
*   **Pinned Algorithms & Mandatory Claims (`CWE-347`, `CWE-287`)**:
    *   Pins `algorithms=("RS256",)` when `jwks_url` is configured and `algorithms=("HS256",)` for symmetric keys—rejecting `alg: none` and RS-to-HS key-confusion attacks.
    *   Requires `exp` (verified), `aud` (when `AGENTOPS_IDENTITY__CLIENT_ID` / `audience` is set), `iss` (when `AGENTOPS_IDENTITY__ISSUER` / `issuer` is set), and `type == expected_type` (`"access"` by default, preventing refresh token misuse).

### 1.2 Symmetric HS256 & Fail-Closed Production Configuration (`CWE-798`)
*   **Zero Hardcoded Fallback Secrets**: Outside development environments (`ENVIRONMENT` not in `{"dev", "development", "local", "test"}`), `JWTAuthEngine` fails closed with `ConfigurationError` if neither `jwks_url` nor `JWT_SECRET_KEY` (minimum 32 UTF-8 bytes) is configured.
*   **Dev-Only Ephemeral Key**: Only when `ENVIRONMENT` is explicitly `dev`, `development`, `local`, or `test` and no key is configured does `JWTAuthEngine` generate a process-local `secrets.token_hex(32)` ephemeral key.
*   **Password Hashing (`CWE-327`)**: `hash_password()` and `verify_password()` use 600,000-iteration PBKDF2-HMAC-SHA256 with 16-byte `os.urandom(16)` salts and constant-time `hmac.compare_digest()`.

### 1.3 Ambient `ExecutionScope` & Anti-Spoofing Decorators (`CWE-290`)
[`sdk/src/agentops_sdk/security/identity.py`](../sdk/src/agentops_sdk/security/identity.py) and [`domain/context.py`](../sdk/src/agentops_sdk/domain/context.py) isolate identity across sync calls and `asyncio` coroutines via `contextvars.ContextVar`:
*   **`@pass_user_identity(token_param="auth_token", jwt_engine=...)`**: Extracts Bearer token from args/kwargs or outer `ExecutionScope`. If absent, clears any leaked outer `identity_token_ctx` before entering the wrapped call; if `jwt_engine` is provided, requires and verifies an `access` JWT (`AuthenticationError` if missing/invalid). `DEV_RBAC_USER` is honored exclusively in dev environments.
*   **`@inject_secret(secret_id, param_name="secret_value", ...)`**: Overwrites caller-supplied `param_name` arguments (including positional-only parameters via `sig.bind_partial` + `apply_defaults()`) with the Secret Manager value so callers cannot spoof injected secrets.

---

## 2. Fail-Closed Secret Management (`SecretManagerStore`, `CWE-636`)

[`sdk/src/agentops_sdk/security/secrets.py`](../sdk/src/agentops_sdk/security/secrets.py) implements `SecretStorePort` with zero fallback to environment variables or defaults:
*   **256-Stripe Singleflight TTL Cache**: `SecretManagerStore` caches secrets in `BoundedTTLCache(maxsize=512, ttl_seconds=300.0)` protected by 256 CRC32-hashed `threading.Lock` stripes and a `10.0s` client timeout.
*   **Multi-Version Revocation Eviction**: On `PermissionDenied`, `NotFound`, or `delete_secret()`, `_evict_secret_versions()` evicts every tracked cached version plus `"latest"` so revoked secrets are never served from stale cache.
*   **Strict Identifier Validation (`CWE-20`)**: `validate_secret_id()` enforces `^[A-Za-z0-9_-]{1,255}$` and `_validate_version()` enforces `^(?:latest|[0-9]{1,20})$`.
*   **Secret-Protected Config (`CWE-798`)**: `DatabaseSettings.password` and `DatabaseSettings.databricks_token` use `pydantic.SecretStr` defaulting to empty ([`config/_schemas.py`](../sdk/src/agentops_sdk/config/_schemas.py)).
*   **Hermetic Test Adapter**: Unit tests inject `InMemorySecretStore` explicitly.

---

## 3. SQL AST Governance & Row-Level Security (`agentops_sdk.sql`, `CWE-89`, `CWE-636`)

### 3.1 Read-Only AST Guardrail (`SQLASTValidator`, `validate_sql_query`)
[`sdk/src/agentops_sdk/sql/ast_guard.py`](../sdk/src/agentops_sdk/sql/ast_guard.py) and [`_ast_rules.py`](../sdk/src/agentops_sdk/sql/_ast_rules.py) enforce single-statement, read-only SQL invariants via `sqlglot`:
*   **Pure Read-Only Roots**: Accepts only single-statement `Select`, `Union`, `Intersect`, or `Except` roots; rejects multi-statement, empty, or non-string payloads.
*   **Forbidden Nodes, TVFs & Hidden Comment DDL**: Blocks DML/DDL/DCL nodes (`Insert`, `Update`, `Delete`, `Drop`, `Alter`, `Create`, `Merge`, `TruncateTable`, `Command`, `Grant`, `Revoke`, etc.), table-valued functions (`read_csv`, etc.) except `UNNEST`, dangerous functions (`SESSION_USER`, `CURRENT_USER`, `EXTERNAL_QUERY`, `PG_READ_FILE`, `PG_SLEEP`, `DBLINK`, etc.), and DDL/DML keywords hidden inside SQL comments.
*   **System Schema & Fail-Closed Allowlist**: Blocks `information_schema`, `pg_catalog`, `pg_toast`, `sys`, `mysql`, `performance_schema`, `system`, and `sqlite_*` system tables. `is_table_in_allowlist()` rejects empty allowlists or bare `"*"` wildcards (`CWE-636`) and anchors dataset wildcards (`dataset.*`) by exact parent prefix segment matching while excluding CTE alias self-shadowing (`collect_physical_tables`).
*   **Decorator Coverage (`@validate_sql_query_decorator`)**: [`sql/decorators.py`](../sdk/src/agentops_sdk/sql/decorators.py) binds signature defaults (`bound.apply_defaults()`), `*args`, and `**kwargs`, ensuring every SQL string argument passes `SQLASTValidator` and tags `sql.ast.is_safe` on the active OTel span.

### 3.2 Parse-Tree Row-Level Security (`RowLevelPolicyEngine`, `apply_row_level_policy`)
[`sdk/src/agentops_sdk/sql/rls.py`](../sdk/src/agentops_sdk/sql/rls.py) and [`_rls_helpers.py`](../sdk/src/agentops_sdk/sql/_rls_helpers.py):
*   **Pre-Validation & Fail-Closed Parsing**: Validates SQL via `validate_sql_query()` first and fails closed with `SQLGovernanceError` on parse errors or missing tenant claims.
*   **Role-Gated Admin Bypass**: Only `role in {"admin", "superadmin"}` bypasses RLS predicate injection; unprivileged claims with string values like `"all"` are enforced literally (`WHERE tenant_id = 'all'`).
*   **Wrapper Unwrapping & Collision Disambiguation**: `unwrap_table_node()` recursively unwraps `Subquery`, `TableSample`, and `Paren` wrappers around physical tables; `matches_target_tables()` matches both qualified and unqualified references for targeted tables; `_build_qualified_column()` preserves schema/catalog qualification when two unaliased tables share a base name.
*   **Compound `OR` Precedence**: Wraps pre-existing `WHERE` clauses in parentheses (`exp.Paren`) before conjoining parenthesized tenant predicates (`(a = 1 OR b = 2) AND (t.tenant_id = 'tenant-01')`).

---

## 4. Fail-Closed Model Armor & Regional Cloud DLP (`agentops_sdk.security`)

*   **Cloud Model Armor (`SecurityGuard`, `CloudModelArmorScanner`)**: Pools regional `modelarmor_v1.ModelArmorClient` clients (`timeout=10`), evaluates all 5 threat filter results + SDP inspect/de-identify, treats `InvocationResult.FAILURE` and `InvocationResult.PARTIAL` (when `fail_closed=True`) as `SecurityViolationError`, and defaults `SecurityGuard(fail_closed=True)` to `SecurityVerdict.BLOCK` (`violation_category="scanner_unavailable"`). See [Model Armor Security Layer Guide](model_armor_security_layer_guide.md).
*   **Cloud DLP (`OutputDLPSanitizer`, `GoogleDLPScrubber`)**: Pools regional `dlp_v2.DlpServiceClient` instances (`timeout=15.0`), chunks UTF-8 payloads at `<= 400,000` bytes, raises `PrivacyScrubError` on errors (no silent regex fallback), and tags `agentops.privacy.pii_scrubbed` on OTel spans. Use `RegexPrivacyScrubber()` explicitly in offline tests. See [Output GDPR & Cloud DLP Sanitization Design](privacy_dlp_design.md).

---

## 5. Zero-Trust MCP Governance Gateway (`mcp_servers/src/`)

[`mcp_servers/src/server.py`](../mcp_servers/src/server.py), [`auth_guard.py`](../mcp_servers/src/auth_guard.py), and [`_http.py`](../mcp_servers/src/_http.py) eliminate credential exposure and pre-auth oracle attacks:
*   **No LLM-Exposed `auth_token` Parameter (`CWE-288`)**: `query_database_tool`, `query_databricks_tool`, and `query_feature_store_tool` omit `auth_token` from tool schemas; HTTP bearer tokens are bound per request via ASGI `HeaderBindingMiddleware` or `ExecutionScope`. `MCP_AUTH_TOKEN` server fallback is removed.
*   **Fail-Closed Auth & Pre-Oracle Ordering (`CWE-306`, `CWE-203`)**: `MCP_REQUIRE_AUTH` defaults to `"true"`. `authorize_query()` verifies the caller's JWT *before* running `SQLASTValidator` (`MCP_ALLOWED_TABLES`) and `RowLevelPolicyEngine`, preventing unauthenticated table-existence enumeration.
*   **Tenant-Scoped Feature Store (`CWE-284`)**: `authorize_feature_store_tenant()` validates `MCP_ALLOWED_ENTITY_TYPES`, `tenant_id` (or `tid`), `allowed_entity_types`, `allowed_entity_ids`, and rejects cross-tenant `tenant:entity_id` keys.
*   **Transport & Container Hardening (`CWE-319`)**: `get_connector_api_url()` requires `https://` for non-loopback `CONNECTOR_API_URL` targets unless `ALLOW_INSECURE_CONNECTOR_HTTP=true`; `MCP_HOST` defaults to `127.0.0.1`. [`mcp_servers/Dockerfile`](../mcp_servers/Dockerfile) builds from repo root under non-root `mcpuser`.

---

## 6. Code Evaluation, Templating, Path/Log Hygiene & Audit (`CWE-94`, `CWE-1336`, `CWE-22/59/732`, `CWE-79/117/532`)

| Control | Module & Symbol | Enforced Guarantee |
| :--- | :--- | :--- |
| **Custom Metric Code AST Allowlist (`CWE-94`)** | [`evaluation/code_validator.py`](../sdk/src/agentops_sdk/evaluation/code_validator.py): `validate_metric_code` | Restricts code to `<= 16,384` chars, 12 safe stdlib imports (`math`, `re`, `json`, `statistics`, etc.), blocks `eval`/`exec`/`open`/`__import__`/`getattr`, private/dunder attributes, and indirect calls. |
| **Bounded Jinja2 Sandbox (`CWE-1336`)** | [`domain/templating.py`](../sdk/src/agentops_sdk/domain/templating.py): `_BoundedSandboxEnvironment` | Restricts globals (`range` capped at `1000`, `dict`), caps sequence `*` multiplication (`<= 256,000` chars) and `**` exponents (`<= 64`), and preserves `{% raw %}` blocks. |
| **Path Containment, No-Symlink & Permissions (`CWE-22`, `CWE-59`, `CWE-732`)** | [`telemetry/_sink_helpers.py`](../sdk/src/agentops_sdk/telemetry/_sink_helpers.py), [`prompts/local_yaml_adapter.py`](../sdk/src/agentops_sdk/prompts/local_yaml_adapter.py), [`pytest_plugin.py`](../sdk/src/agentops_sdk/pytest_plugin.py), [`tools/observability/log_exporter.py`](../tools/observability/log_exporter.py) | Rejects `..`, symlinks (`O_NOFOLLOW`, `ELOOP`), system roots (`/etc`, `/bin`, `/usr`, `/System`), and base-dir escapes; enforces `0o700` dirs and `0o600` file permissions with atomic writes. |
| **Report XSS & URL Safety (`CWE-79`)** | [`evaluation/reporting.py`](../sdk/src/agentops_sdk/evaluation/reporting.py): `_esc`, `_safe_url` | Escapes Markdown/HTML control chars in test tables and restricts console URLs to `https://` without userinfo or markdown delimiters. |
| **Log & Span Redaction (`CWE-117`, `CWE-532`)** | [`domain/validation.py`](../sdk/src/agentops_sdk/domain/validation.py): `sanitize_log_field`, `scrub_sensitive_text` | Strips CR/LF/control chars from log fields and redacts Bearer tokens, emails, and phones from OTel exceptions, tool errors, and `AuditLedger` error payloads. |
| **Shell Argument Safety (`CWE-78`)** | [`tools/observability/setup_log_routers.sh`](../tools/observability/setup_log_routers.sh) | Passes shell variables strictly via `sys.argv[1..4]` into a single-quoted `<<'EOF'` Python heredoc. |

---

## 7. Security Verification Suites

All 23 remediated CWE classes and adapter contracts are verified hermetically (with non-loopback network sockets blocked by [`tests/conftest.py`](../tests/conftest.py)):

```bash
# Run all security, SQL governance, MCP, and CWE regression unit & contract tests
pytest -v \
  tests/unit/test_security_auth.py \
  tests/unit/test_security_guards.py \
  tests/unit/test_security_cwe_regressions.py \
  tests/unit/test_security_cwe_io_and_templates.py \
  tests/unit/test_sql_governance.py \
  tests/unit/test_mcp_server.py \
  tests/contract/test_security_contract.py \
  --eval-mode=in_memory
```
