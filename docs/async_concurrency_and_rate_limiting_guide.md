<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# Enterprise Guide: Asynchronous Rate Limiting, Concurrency Sizing & Zero-Trust Security (`v1.0.0`)

**Author:** Google Professional Services / Google Customer AI Platform Engineering  
**Version:** 1.0.0 (Hexagonal Architecture & Concurrency Isolation)  
**Status:** Active Production Standard  
**Branch:** `main`  

---

## 1. Executive Summary & Architectural Overview

High-throughput, multi-agent enterprise AI architectures—including Google Agent Development Kit (ADK) autonomous agents, LangGraph orchestrators, Microsoft Copilot Studio bots, and Model Context Protocol (MCP) tool runners—issue bursts of LLM calls and analytical queries against GCP and enterprise data engines:
*   **Google Cloud BigQuery & BigLake** (Serverless OLAP)
*   **Vertex AI Gemini / Model APIs** (Token & RPM-quota-governed inference)
*   **Vertex AI Feature Online Store (v2)** (Low-latency online feature serving)
*   **Upstream Connector Endpoints** (`${CONNECTOR_API_URL}/v1/query` behind the MCP Gateway)

Uncontrolled concurrent bursts risk triggering `429` cloud quota rejections, causing "noisy neighbor" starvation across tenants, or locking `asyncio` event loops. `agentops_sdk.resilience` ([`rate_limiter.py`](../sdk/src/agentops_sdk/resilience/rate_limiter.py), [`_rate_core.py`](../sdk/src/agentops_sdk/resilience/_rate_core.py)) provides thread-safe and loop-isolated rate limiting alongside bounded concurrency control, while `agentops_sdk.security`, `agentops_sdk.sql`, and `mcp_servers/src/` enforce zero-trust JWT authentication and AST Row-Level Security.

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        AI Agent / Enterprise Client Applications                       │
│            (Google ADK, LangGraph, Copilot Studio, Autonomous MCP Clients)             │
│                  Sends Authorization: Bearer <JWT> & W3C traceparent                   │
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│               SDK Resilience & Security Layer (`agentops_sdk`)                         │
│  • @rate_limit / AsyncRateLimiter / SyncRateLimiter                                    │
│    - TOKEN_BUCKET, SLIDING_WINDOW, CONCURRENCY_SEMAPHORE                               │
│    - (rule_id, key) BoundedTTLCache (maxsize=10,000, ttl=3600s)                        │
│    - Verified ExecutionScope claims precedence (key_func -> tenant_id/tid -> user_id)  │
│    - Loop-isolated asyncio.Lock (WeakKeyDictionary) + threading.Lock _state_lock       │
│  • JWTAuthEngine (RS256 SafeJWKClient SSRF Guard / HS256)                              │
│  • SQLASTValidator + RowLevelPolicyEngine (sqlglot AST rewrite)                        │
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│           Hardened MCP Governance Gateway (`mcp_servers/src/server.py`)                │
│  • HeaderBindingMiddleware binds HTTP Bearer JWT (no LLM auth_token parameter)         │
│  • Auth BEFORE SQL AST oracle (`authorize_query`) & Feature Store tenant isolation     │
│  • Forwards validated payloads over HTTPS (`_REQUEST_TIMEOUT_SECONDS = 15.0`)          │
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│   Downstream Services (BigQuery / Vertex AI Feature Store / Connector Gateway API)     │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Rate Limiting Algorithms & Concurrency Architecture (`agentops_sdk.resilience`)

`RateLimiterCore` ([`sdk/src/agentops_sdk/resilience/_rate_core.py`](../sdk/src/agentops_sdk/resilience/_rate_core.py)) implements three explicit algorithms via `RateLimitAlgorithm` (`StrEnum` in [`domain/models.py`](../sdk/src/agentops_sdk/domain/models.py)):

1.  **`RateLimitAlgorithm.TOKEN_BUCKET`**:
    *   Allows controlled bursts up to `burst` tokens while replenishing continuously at `rate / period_seconds` tokens per second.
    *   Default algorithm for `@rate_limit`. Ideal for bursty multi-tool agent chains.
2.  **`RateLimitAlgorithm.SLIDING_WINDOW`**:
    *   Maintains monotonic `collections.deque[float]` request timestamps over `period_seconds`, pruning expired timestamps (`<= now - period_seconds`) on every check.
    *   Enforces strict ceiling compliance without boundary spikes.
3.  **`RateLimitAlgorithm.CONCURRENCY_SEMAPHORE`**:
    *   Enforces `max_concurrency` on active in-flight executions (`state.in_flight`), independent of request-per-period replenishment.
    *   Also enforced as a pre-check on `TOKEN_BUCKET` and `SLIDING_WINDOW` whenever `rule.max_concurrency > 0`.

### Thread Safety, Event-Loop Isolation & TTL Eviction
*   **Tuple Bucket Keys (`(rule_id, key)`)**: Isolates quotas cleanly per rule and per tenant/user so multiple `@rate_limit` rules never collide on shared bucket state.
*   **Bounded TTL State Store**: Bucket states (`BucketState`) reside in `BoundedTTLCache(maxsize=10_000, ttl_seconds=3600.0)` protected by `threading.Lock` (`_state_lock`), preventing unbounded memory growth.
*   **Sleep Outside Locks**: Both `SyncRateLimiter.acquire()` (`time.sleep`) and `AsyncRateLimiter.acquire()` (`await asyncio.sleep`) sleep strictly outside `_state_lock` and per-loop `asyncio.Lock` (tracked via `weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]`).
*   **Symmetric Slot Release**: `@rate_limit` wraps execution in `try ... finally: active.release(...)`, guaranteeing concurrency slots and `agentops.rate_limit.concurrency.active` gauges return to zero even when the wrapped function raises an exception.

---

## 3. Verified Identity Keying & `RateLimitExceededError` (`CWE-290`)

When using `@rate_limit`, `_resolve_scope_key` determines the bucket key in strict security precedence order:
1.  **Explicit `key_func(*args, **kwargs)`** (if provided).
2.  **Verified `ExecutionScope` claims**: `scope.claims.get("tenant_id") or scope.claims.get("tid")` -> `"tenant:{tid}"`.
3.  **Authenticated `ExecutionScope.user_id`** -> `"user:{user_id}"`.
4.  **Unverified `tenant_id` kwarg** (ONLY if `trust_unverified_kwargs=True`, which defaults to `False` to prevent tenant-key spoofing across callers).
5.  Fallback key `"global"`.

If a request is rejected (either immediately when `wait_timeout_seconds=0.0` or after bounded queuing expires), `@rate_limit` raises `RateLimitExceededError` ([`domain/exceptions.py`](../sdk/src/agentops_sdk/domain/exceptions.py)):
*   `exc.key`: Resolved bucket scope key (e.g., `"tenant:tenant-emea"`)
*   `exc.retry_after_seconds`: Computed wait duration before the next token/slot opens
*   `exc.status_code`: `429`

### Developer Usage Example (see `examples/full_stack/`)

```python
from agentops_sdk.domain.context import bind_execution_scope
from agentops_sdk.domain.exceptions import RateLimitExceededError
from agentops_sdk.domain.models import RateLimitAlgorithm, RateLimitRule
from agentops_sdk.resilience import AsyncRateLimiter, rate_limit


@rate_limit(
    rule_id="supplier-catalog",
    rate=60,
    period_seconds=60.0,
    burst=10,
    max_concurrency=5,
    algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
)
async def fetch_supplier_parts(supplier_id: str) -> dict[str, str]:
    return {"supplier_id": supplier_id, "status": "ok"}


async def call_for_tenant() -> dict[str, str]:
    with bind_execution_scope(
        user_id="alice@example.com",
        claims={"tenant_id": "tenant-emea"},
    ):
        try:
            return await fetch_supplier_parts("SUPPLIER-001")
        except RateLimitExceededError as exc:
            raise RuntimeError(
                f"Throttled ({exc.status_code}): retry in {exc.retry_after_seconds:.2f}s"
            ) from exc
```

---

## 4. Infrastructure Concurrency Sizing & Upstream Connector Budgets

> [!NOTE]
> **Architecture Boundary (`v1.0.0`)**: Data access routes through the hardened **MCP Governance Gateway** (`mcp_servers/src/server.py`) to upstream customer connector endpoints (`CONNECTOR_API_URL`) or directly via GCP-native adapters (`FeatureStoreOnlineClient`, BigQuery).

When operating an upstream connector service behind `mcp_servers/src/server.py`, apply these concurrency and nested-timeout sizing principles:
*   **Dedicated Per-Engine Bounded Pools**: Size engine worker pools according to backend concurrency profiles (for example, BigQuery gRPC channels: `16` workers, external SQL warehouses: `8` workers, Vertex AI Feature Store: `16` workers) rather than sharing Python's default `ThreadPoolExecutor`.
*   **Nested Timeout Budgets**: Ensure inner timeouts expire before outer HTTP/MCP budgets (`_REQUEST_TIMEOUT_SECONDS = 15.0` in `mcp_servers/src/server.py` wrapping `<= 12s` gateway / `<= 10s` query / `<= 2s` pool acquisition budgets), shedding load with `HTTP 503` + `Retry-After` or `HTTP 429` before caller timeouts occur.
*   **Horizontal Quota Coordination**: `AsyncRateLimiter` and `SyncRateLimiter` enforce per-process/container limits with zero external broker dependencies. If strict global cross-replica RPM limits are required across horizontally scaled Cloud Run instances, front the gateway with Cloud Armor rate-limiting rules or an external Redis/Memorystore policy layer.

---

## 5. OpenTelemetry Metrics & Dynamic Configuration

### Recorded OTel Instruments (`_rate_core.py`)
Metric attributes are kept low-cardinality (never attaching raw `tenant_id` to metric labels):
*   `agentops.rate_limit.requests.total` (Counter): `{"allowed": "True" | "False"}`
*   `agentops.rate_limit.rejected.total` (Counter): incremented on rejected acquisitions
*   `agentops.rate_limit.concurrency.active` (UpDownCounter): `+1` on allow, `-1` on `release()`

### Configuration Reference (`RateLimitSettings` & `DatabaseSettings`)
`RateLimitSettings` ([`sdk/src/agentops_sdk/config/_schemas.py`](../sdk/src/agentops_sdk/config/_schemas.py)) is evaluated dynamically on `acquire()` so toggling `RATE_LIMIT_ENABLED=false` (and clearing settings cache if needed) takes effect cleanly:

| Environment Variable | Settings Class & Field | Default | Description |
| :--- | :--- | :--- | :--- |
| `RATE_LIMIT_ENABLED` | `RateLimitSettings.enabled` | `True` | Master toggle checked on every `acquire()` call. |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | `RateLimitSettings.requests_per_minute` | `120` | Default requests per 60 seconds when no explicit rule matches. |
| `RATE_LIMIT_BURST_SIZE` | `RateLimitSettings.burst_size` | `30` | Default token bucket burst capacity. |
| `RATE_LIMIT_MAX_CONCURRENCY` | `RateLimitSettings.max_concurrency` | `25` | Default concurrent in-flight ceiling (`0` disables concurrency cap). |
| `RATE_LIMIT_WAIT_TIMEOUT_SECONDS` | `RateLimitSettings.wait_timeout_seconds` | `0.0` | Max seconds to sleep waiting for capacity (`0.0` fails fast). |
| `DB_TYPE` | `DatabaseSettings.type` | `"bigquery"` | Allowed values: `snowflake`, `bigquery`, `postgres`, `sap_hana`, `databricks`, `feature_store`. |

---

## 6. Verification & E2E Test Suites

### Unit Tests & Runnable Example
```bash
# Run resilience unit tests (rate limiters, loop isolation, claim scoping, tool validator)
pytest -v tests/unit/test_resilience.py --eval-mode=in_memory

# Run the full-stack hermetic demonstration (includes rate-limiting burst)
MODE=hermetic python examples/full_stack/run_full_stack.py --hermetic
```

### Automated E2E Shell Suite (`tests/e2e_async_rate_limiting/`)

| Script | Coverage |
| :--- | :--- |
| [`run_all_rate_limiting_tests.sh`](../tests/e2e_async_rate_limiting/run_all_rate_limiting_tests.sh) | Master runner executing all 4 E2E tests (`local-offline` or `<your-gcp-project>`). |
| [`test_1_jwt_auth_and_rls_security.sh`](../tests/e2e_async_rate_limiting/test_1_jwt_auth_and_rls_security.sh) | `JWTAuthEngine`, `auth_guard` mock/forged token rejection, `SQLASTValidator`, `RowLevelPolicyEngine`, and Feature Store tenant check. |
| [`test_2_async_rate_limiting_tiers.sh`](../tests/e2e_async_rate_limiting/test_2_async_rate_limiting_tiers.sh) | `TOKEN_BUCKET`, `SLIDING_WINDOW`, `RateLimitExceededError`, and multi-tenant isolation. |
| [`test_3_concurrency_limits_and_metrics.sh`](../tests/e2e_async_rate_limiting/test_3_concurrency_limits_and_metrics.sh) | `CONCURRENCY_SEMAPHORE`, exception slot release symmetry, and OTel `agentops.rate_limit.*` metrics. |
| [`test_4_adk_agent_rate_limiting.sh`](../tests/e2e_async_rate_limiting/test_4_adk_agent_rate_limiting.sh) | End-to-end ADK agent rate-limiting run (`local-offline` or live GCP). |
| [`demo_5min_presentation.sh`](../tests/e2e_async_rate_limiting/demo_5min_presentation.sh) | Guided interactive (`--auto` supported) 5-minute walkthrough. |

```bash
# Run offline E2E suite
bash tests/e2e_async_rate_limiting/run_all_rate_limiting_tests.sh local-offline

# Run against live GCP project
bash tests/e2e_async_rate_limiting/run_all_rate_limiting_tests.sh <your-gcp-project>
```
