# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Tutorial 03: rate limiting with @rate_limit and OTel Prometheus metrics."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared import settings
from _shared import telemetry

cfg = settings.load("03_rate_limiting")
tel = telemetry.start(cfg)

from agentops_sdk import AsyncRateLimiter
from agentops_sdk import RateLimitAlgorithm
from agentops_sdk import RateLimitExceededError
from agentops_sdk import rate_limit
from agentops_sdk import trace_agent_span

LIMITER = AsyncRateLimiter()


def price_key(*_args, **kwargs) -> str:
    """Scope price-lookup rate limits per dealer ID."""
    return f"price:{kwargs['dealer_id']}"


def sync_key(*_args, **kwargs) -> str:
    """Scope bulk-sync concurrency caps per dealer ID."""
    return f"sync:{kwargs['dealer_id']}"


@rate_limit(
    rule_id="price-lookup",
    rate=5,
    period_seconds=10.0,
    max_concurrency=50,
    algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
    key_func=price_key,
    limiter=LIMITER,
    wait_timeout_seconds=0.0,
)
@trace_agent_span(agent_name="price_lookup")
async def fetch_price(*, dealer_id: str, part_number: str) -> float:
    """Simulated supplier price API call."""
    del dealer_id, part_number
    await asyncio.sleep(0.05)
    return 129.90


@rate_limit(
    rule_id="bulk-sync",
    rate=1000,
    period_seconds=60.0,
    max_concurrency=2,
    key_func=sync_key,
    limiter=LIMITER,
)
@trace_agent_span(agent_name="bulk_sync")
async def bulk_sync(*, dealer_id: str) -> int:
    """Simulated bulk price-list sync limited to 2 concurrent runs."""
    del dealer_id
    await asyncio.sleep(0.3)
    return 1


async def burst(tool, calls: int, **kwargs) -> tuple[int, int]:
    """Fire `calls` concurrent invocations and tally allowed vs rejected."""

    async def one() -> bool:
        try:
            await tool(**kwargs)
            return True
        except RateLimitExceededError as exc:
            print(f"  rejected: {str(exc)[:90]}")
            return False

    results = await asyncio.gather(*(one() for _ in range(calls)))
    allowed = sum(results)
    return allowed, calls - allowed


def _verify_prometheus() -> None:
    tel.flush()
    print("\nwaiting for Prometheus to scrape ...")
    time.sleep(8)
    for name in (
        "agentops_rate_limit_requests_total",
        "agentops_rate_limit_rejected_total",
    ):
        rows = telemetry.prometheus_query(
            f'sum({name}{{instance="{cfg.run_id}"}})'
        )
        value = rows[0][1] if rows else None
        print(f"  {name} = {value}")
        if value is None:
            sys.exit(
                f"{name} did not reach Prometheus. Check: bash "
                "examples/tutorial/observability/stack.sh logs alloy"
            )
    print(f"\nGrafana instance filter: {cfg.run_id}")


async def main() -> None:
    """Exercise sliding-window and concurrency rate limits."""
    print("\n1) sliding window 5 per 10 s, 12 calls:")
    ok_1, rej_1 = await burst(
        fetch_price, 12, dealer_id="dealer-4711", part_number="PART-1001"
    )
    print(f"   -> allowed={ok_1} rejected={rej_1}")

    print("\n2) concurrency cap 2, 6 calls at once:")
    ok_2, rej_2 = await burst(bulk_sync, 6, dealer_id="dealer-4711")
    print(f"   -> allowed={ok_2} rejected={rej_2}")
    print(f"   totals: allowed={ok_1 + ok_2}, rejected={rej_1 + rej_2}")

    _verify_prometheus()


if __name__ == "__main__":
    asyncio.run(main())
