# Security, Auth & SQL Governance (`v1.0.0`)

## 1. JWT, RS256, Entra ID & JWKS SSRF Defense

- **`JWTAuthEngine`** (`sdk/src/agentops_sdk/security/auth.py`):
  - Pins `("RS256",)` for JWKS/PEM and `("HS256",)` for symmetric secrets (`>= 32` bytes outside dev).
  - **Fail-Closed `RS256`**: Requires both `audience` and `issuer` (`ConfigurationError` otherwise) and enforces `exp`, `aud`, and `iss` claims.
  - **Static RSA PEM**: `parse_rsa_public_pem()` supports `JWT_PUBLIC_KEY` / `MCP_JWT_PUBLIC_KEY`.
  - **Entra ID Support**: `ENTRA_JWKS_URL`, `ENTRA_CLIENT_ID`, `ENTRA_ISSUER`, `ENTRA_TENANT_ID`, and `build_entra_issuer()` (rejects `common`/`organizations`/`consumers`); allows OIDC typeless `RS256` access tokens while rejecting `"refresh"`.
  - **SSRF Defense** (`_jwks.py`): `validate_jwks_url()` enforces `https://`, blocks private/loopback/CGNAT/link-local IPs, pins allowed hosts (`AGENTOPS_IDENTITY__ALLOWED_JWKS_HOSTS`), and blocks 3xx redirects.

## 2. Scope-Aware SQL AST & Row-Level Security (`sqlglot>=30.0.0`)

- **`SQLASTValidator`** (`ast_guard.py`): Read-only `SELECT`/set-op root enforcement, blocks DDL/DML and `exp.TableFromRows`, rejects system schemas, and permits `ML.PREDICT` only as sole `FROM` source with zero joins.
- **`RowLevelPolicyEngine`** (`rls.py`):
  - Dialect-normalized CTE scoping (`visible_cte_names`, `enclosing_cte_names`).
  - `LEFT JOIN ... ON` predicate injection (preserving unmatched outer rows) vs. `USING`/`NATURAL` fallback.
  - Rejects `PIVOT`/`UNPIVOT`, column-list aliases (`AS t(a, b)`), joined TVFs, and empty `target_tables=[]`.
  - Wildcard patterns (`dataset.*`) + post-rewrite `_verify_all_tables_covered()` fail-closed verification.

## 3. Model Armor, Cloud DLP & Audit Ledger

- **`SecurityGuard` / `CloudModelArmorScanner`**: `fail_closed=True` default.
- **`OutputDLPSanitizer` / `GoogleDLPScrubber`**: Regional Cloud DLP de-identification (`dlp.{location}.rep.googleapis.com`).
- **`AuditLedger`**: Defaults to `CloudLoggingAuditAdapter` (`agentops-audit-ledger`); supports `BigQueryAuditAdapter`, `LocalFileAuditAdapter`, and `InMemoryAuditAdapter`.

See [docs/security_and_verification.md](../docs/security_and_verification.md) and [docs/audit_ledger_design.md](../docs/audit_ledger_design.md).
