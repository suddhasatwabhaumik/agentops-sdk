# 07 · JWT Auth: Sign with a Key in Secret Manager, Verify Everywhere (`v1.0.0`)

**What you learn:** how an agent or MCP gateway fetches a signing key from Google Secret Manager via `SecretManagerStore`, signs short-lived access tokens with `JWTAuthEngine` (`pyjwt>=2.10.0`), and validates tampering and expiry cleanly.

```
SecretManagerStore.get_secret("adk-jwt-secret-key")
          │
          ▼
JWTAuthEngine(secret_key=signing_key, access_ttl_seconds=900)
  ├─ .create_access_token(claims)               ──► HS256 JWT (15-min TTL)
  ├─ .decode_token(token, expected_type="access") ──► verified claims (raises AuthenticationError on tamper/expiry)
  └─ TUTORIAL_STORE_TOKEN=1 (opt-in)            ──► store.put_secret("agentops-tutorial-token-unit-001", token)
```

## SDK calls used

| Call | What it does |
|---|---|
| `SecretManagerStore(project_id=...)` | Reads (`get_secret`) and writes (`put_secret`) secrets in Google Secret Manager with TTL in-memory caching |
| `JWTAuthEngine(*, secret_key=..., jwks_url=..., access_ttl_seconds=900, ...)` | Issues and verifies HS256 access/refresh JWTs or corporate RS256 JWKS tokens |
| `engine.create_access_token(claims, *, ttl_seconds=None)` | Signs an access JWT (`token_type="access"`) |
| `engine.decode_token(token, *, expected_type="access")` | Validates signature, expiration, and token type; raises `AuthenticationError` on invalid/expired/tampered input |

`JWTAuthEngine` enforces fail-closed semantics with zero built-in fallback signing secrets. For corporate Entra ID / `RS256` federation, configure `jwks_url=...` (SSRF-guarded via `validate_jwks_url` and `AGENTOPS_IDENTITY__ALLOWED_JWKS_HOSTS`) or static RSA PEM `public_key` (`JWT_PUBLIC_KEY` / `MCP_JWT_PUBLIC_KEY`) alongside mandatory `audience` and `issuer` (also supporting `ENTRA_*` env aliases with single-tenant issuer derivation and `"tid"` -> `"tenant_id"` RLS mapping).

## GCP resources used

| Resource | Why | Access needed |
|---|---|---|
| Secret Manager secret `adk-jwt-secret-key` | HS256 signing key (read) | `roles/secretmanager.secretAccessor` |
| Secret Manager secret `agentops-tutorial-token-unit-001` | Opt-in persistence when `TUTORIAL_STORE_TOKEN=1` | `roles/secretmanager.admin` |

## Run

```bash
python examples/tutorial/07_jwt_auth/main.py
TUTORIAL_STORE_TOKEN=1 python examples/tutorial/07_jwt_auth/main.py   # optional: persist token in Secret Manager
```

Expected: valid token verifies, refresh/tampered/expired checks raise `AuthenticationError` cleanly across steps 1–5.
