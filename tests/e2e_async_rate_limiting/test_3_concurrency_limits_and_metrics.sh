#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 3: Concurrency Limits, Clean Slot Release & Real OTel Metrics (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

echo "[TEST 3 INFO] Running Concurrency Limits & OpenTelemetry Metrics Tests..."

"${PYTHON_BIN}" - << 'PYEOF'
import asyncio

from agentops_sdk import AsyncRateLimiter
from agentops_sdk import RateLimitAlgorithm
from agentops_sdk import RateLimitExceededError
from agentops_sdk import RateLimitRule
from agentops_sdk import rate_limit
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


def collect_metrics(reader: InMemoryMetricReader) -> dict[str, list[tuple[dict, float]]]:
    out: dict[str, list[tuple[dict, float]]] = {}
    data = reader.get_metrics_data()
    if not data:
        return out
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                pts = [(dict(dp.attributes), float(dp.value)) for dp in m.data.data_points]
                out[m.name] = pts
    return out


async def main() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)

    limiter = AsyncRateLimiter()
    rule = RateLimitRule(
        rule_id="conc-sem",
        rate=100,
        period_seconds=60.0,
        burst=100,
        max_concurrency=2,
        algorithm=RateLimitAlgorithm.CONCURRENCY_SEMAPHORE,
    )

    print("[3.1] Verifying single-slot concurrency cap & release...")
    r1 = await limiter.acquire("k1", rule=rule)
    r2 = await limiter.acquire("k1", rule=rule)
    r3 = await limiter.acquire("k1", rule=rule)
    assert r1.allowed and r2.allowed and not r3.allowed
    await limiter.release("k1", rule=rule)
    await limiter.release("k1", rule=rule)

    r4 = await limiter.acquire("k1", rule=rule)
    assert r4.allowed is True
    await limiter.release("k1", rule=rule)

    print("[3.2] Verifying @rate_limit releases concurrency slot on exception...")
    @rate_limit(
        rule_id="fail-safe-release",
        rate=10,
        period_seconds=60.0,
        burst=10,
        max_concurrency=1,
        limiter=limiter,
    )
    async def boom() -> None:
        raise RuntimeError("simulated tool failure")

    for _ in range(3):
        try:
            await boom()
        except RuntimeError:
            pass
        except RateLimitExceededError as exc:
            raise AssertionError(f"Slot leaked after exception: {exc}") from exc

    print("[3.3] Verifying OTel rate-limit metrics & active concurrency == 0...")
    recorded = collect_metrics(reader)
    assert "agentops.rate_limit.requests.total" in recorded
    assert "agentops.rate_limit.rejected.total" in recorded
    assert "agentops.rate_limit.concurrency.active" in recorded
    active_vals = [v for _, v in recorded["agentops.rate_limit.concurrency.active"]]
    assert sum(active_vals) == 0.0, f"Expected active concurrency 0, got {active_vals}"
    print(f"  => Verified OTel instruments: {sorted(recorded.keys())}")
    provider.shutdown()


asyncio.run(main())
print("ALL CONCURRENCY AND METRICS CHECKS PASSED.")
PYEOF

echo "[TEST 3 PASS] Concurrency limits, slot symmetry, and OTel metrics verified."
