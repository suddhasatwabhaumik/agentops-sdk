# 08 · Identity and Secrets: Caller Context Propagation & Secret Injection (`v1.0.0`)

**What you learn:** how caller identity propagates cleanly across nested tool calls via `ContextVar`-backed `ExecutionScope` (`@pass_user_identity`), and how `@inject_secret` resolves third-party API credentials from Secret Manager with TTL caching.

```
request(auth_token=jwt) ──► @pass_user_identity() ──► ContextVar ExecutionScope
                                  │
                           handle_request()
                             ├─ whoami_tool()     get_user_identity_token() ──► decode JWT claims
                             └─ supplier_tool()   @inject_secret("agentops-tutorial-supplier-api-key", param_name="secret_payload")
                                                     └──► SecretManagerStore (TTL-cached) ──► secret_payload kwarg
```

## SDK calls used

| Call | What it does |
|---|---|
| `@pass_user_identity(*, token_param="auth_token", jwt_engine=None)` | Binds caller bearer token and decoded claims into `ExecutionScope` for the duration of the call, clearing cleanly on exit |
| `get_user_identity_token()` | Reads the active caller token from `ExecutionScope` inside nested tools |
| `@inject_secret(secret_id, *, param_name="secret_value", store=...)` | Resolves a Secret Manager secret dynamically at call time and injects it as a keyword argument |
| `SecretManagerStore(project_id=...)` | GCP Secret Manager backend with TTL caching (`get_secret`, `put_secret`, `delete_secret`); pair with `InMemorySecretStore` in unit tests |
| `SecretAccessError` | Raised when the target secret does not exist or permissions are insufficient |

Because `ExecutionScope` relies on Python `contextvars.ContextVar` instead of thread-locals, caller identity remains strictly isolated across concurrent `asyncio` tasks.

## GCP resources used

| Resource | Why | Access needed |
|---|---|---|
| Secret Manager secret `agentops-tutorial-supplier-api-key` | Third-party credential injected into `supplier_tool` | Read: `roles/secretmanager.secretAccessor`; initial write (`TUTORIAL_STORE_SECRET=1`): `roles/secretmanager.admin` |
| Secret Manager secret `adk-jwt-secret-key` | Signs the tutorial demo token | `roles/secretmanager.secretAccessor` |

## Run

```bash
TUTORIAL_STORE_SECRET=1 python examples/tutorial/08_identity_and_secrets/main.py   # first time: creates secret
python examples/tutorial/08_identity_and_secrets/main.py                           # subsequent runs: read + cache
```

Expected: `whoami_tool` prints the decoded identity from `ExecutionScope`, and `supplier_tool` receives the injected `secret_payload` from Secret Manager (or reports missing secret cleanly if `TUTORIAL_STORE_SECRET=1` has not been run yet).
