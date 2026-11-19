# 03 · Rate Limiting: Keyed Quotas per Tenant as Metrics (`v1.0.0`)

**What you learn:** how to enforce per-dealer request quotas and max-concurrency caps on async tools, handle `RateLimitExceededError`, and observe allowed vs rejected counts in Prometheus.

```
12 calls ──► fetch_price @rate_limit(rule_id="price-lookup", rate=5, period_seconds=10, key_func=price_key)
                ──► 5 allowed, 7 raise RateLimitExceededError (sliding window)
 6 calls ──► bulk_sync   @rate_limit(rule_id="bulk-sync", max_concurrency=2, key_func=sync_key)
                ──► 2 allowed, 4 raise RateLimitExceededError (in-flight cap)
                                     │
                                     └─► OTel counters agentops.rate_limit.{requests,rejected}.total
                                               │ OTLP every 5 s
                                               ▼
                              Alloy ─► Prometheus (agentops_rate_limit_rejected_total{instance=run_id})
```

## SDK calls used

| Call | What it does |
|---|---|
| `@rate_limit(rule_id, rate, period_seconds, max_concurrency, algorithm, key_func, limiter, wait_timeout_seconds)` | Guards a sync or async callable; buckets are keyed independently by `(rule_id, key)` |
| `AsyncRateLimiter()` | Async state holder managing per-`(rule_id, key)` token buckets, sliding windows, and concurrency semaphores |
| `RateLimitAlgorithm.SLIDING_WINDOW` / `TOKEN_BUCKET` | Exact sliding-window call counting vs token-bucket burst smoothing |
| `RateLimitExceededError` | Raised cleanly when a call violates quota or concurrency limits (`wait_timeout_seconds=0.0`) |

Design note: keying by `rule_id` alongside `key_func` prevents two decorated tools sharing a dealer key from colliding inside the same limiter state.

## GCP resources used

None. This capability is pure Python (`agentops_sdk.resilience`). Telemetry goes exclusively to the local OTLP stack.

## Run

```bash
python examples/tutorial/03_rate_limiting/main.py
```

Expected: `allowed=5 rejected=7` for `fetch_price`, `allowed=2 rejected=4` for `bulk_sync`, followed by Prometheus query results showing `18` total requests and `11` rejections for this `run_id`. In Grafana, view **AgentOps SDK - Overview → Rate limiting**.

## Limits of the in-process limiter

`AsyncRateLimiter` and `SyncRateLimiter` maintain per-process state. Two Cloud Run instances each enforce independent local quotas unless coordinated behind an external rate-limiting proxy or future shared store adapter.
