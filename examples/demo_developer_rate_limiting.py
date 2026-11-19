#!/usr/bin/env python3
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""AgentOps SDK example: rate limiting and multi-tenant isolation.

Demonstrates ``@rate_limit``, ``@trace_agent_span``, ``ExecutionScope``,
``AsyncRateLimiter``, ``SyncRateLimiter``, and ``RateLimitExceededError``.
"""

import asyncio
import time
from typing import Any

from agentops_sdk import AsyncRateLimiter
from agentops_sdk import RateLimitAlgorithm
from agentops_sdk import RateLimitExceededError
from agentops_sdk import RateLimitRule
from agentops_sdk import SyncRateLimiter
from agentops_sdk import bind_execution_scope
from agentops_sdk import configure_telemetry
from agentops_sdk import rate_limit
from agentops_sdk import reset_telemetry
from agentops_sdk import trace_agent_span

RULE_ID = "supplier-catalog"


class CustomerSupplyChainAgent:
    """Supply chain agent protected by rate limiting and tracing."""

    @trace_agent_span(agent_name="query_supplier_catalog")
    @rate_limit(
        rule_id=RULE_ID,
        rate=5,
        period_seconds=10.0,
        burst=3,
        max_concurrency=3,
        algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
    )
    async def fetch_supplier_parts(
        self, supplier_id: str, tenant_id: str
    ) -> dict[str, Any]:
        """Simulate a rate-limited vendor catalog lookup."""
        await asyncio.sleep(0.05)
        return {
            "supplier_id": supplier_id,
            "tenant_id": tenant_id,
            "status": "in_stock",
            "parts_found": [f"PART-{i}" for i in range(101, 104)],
            "timestamp": time.time(),
        }


async def _run_tenant_burst(agent: CustomerSupplyChainAgent) -> None:
    """Exercise burst capacity and capture RateLimitExceededError."""
    print("\n[1/4] Burst requests for tenant-emea (burst=3, rate=5/10s)")
    with bind_execution_scope(
        user_id="alice@example.com",
        claims={"tenant_id": "tenant-emea"},
    ):
        for idx in range(1, 4):
            t0 = time.perf_counter()
            res = await agent.fetch_supplier_parts(
                f"SUPPLIER-00{idx}", tenant_id="tenant-emea"
            )
            ms = (time.perf_counter() - t0) * 1000
            print(
                f"  Request #{idx} allowed: {res['supplier_id']} ({ms:.1f}ms)"
            )

        try:
            await agent.fetch_supplier_parts(
                "SUPPLIER-004", tenant_id="tenant-emea"
            )
            raise AssertionError(
                "Expected request #4 to exceed burst capacity."
            )
        except RateLimitExceededError as exc:
            print(
                f"  Request #4 throttled ({exc.status_code}): {exc} "
                f"[key={exc.key}, retry_after={exc.retry_after_seconds:.2f}s]"
            )


async def _run_tenant_isolation(agent: CustomerSupplyChainAgent) -> None:
    """Verify tenant-na has an isolated rate-limit bucket via ExecutionScope."""
    print("\n[2/4] Multi-tenant isolation for tenant-na via ExecutionScope")
    with bind_execution_scope(
        user_id="bob@example.com",
        claims={"tenant_id": "tenant-na"},
    ):
        res = await agent.fetch_supplier_parts(
            "SUPPLIER-NA-001", tenant_id="tenant-na"
        )
        print(f"  tenant-na allowed cleanly: {res['supplier_id']}")


async def _run_explicit_async_limiter() -> None:
    """Demonstrate direct AsyncRateLimiter usage with RateLimitRule."""
    print("\n[3/4] Explicit AsyncRateLimiter check")
    limiter = AsyncRateLimiter()
    rule = RateLimitRule(
        rule_id="parts-api",
        rate=2,
        period_seconds=10.0,
        burst=2,
        max_concurrency=2,
        algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
    )
    for idx in range(1, 4):
        result = await limiter.acquire("tenant-eu", rule=rule)
        print(
            f"  Attempt #{idx}: allowed={result.allowed}, "
            f"remaining={result.remaining}, key={result.key}"
        )
        if result.allowed:
            await limiter.release("tenant-eu", rule=rule)


def _run_explicit_sync_limiter() -> None:
    """Demonstrate direct SyncRateLimiter usage with RateLimitRule."""
    print("\n[4/4] Explicit SyncRateLimiter check")
    limiter = SyncRateLimiter()
    rule = RateLimitRule(
        rule_id="sync-erp",
        rate=1,
        period_seconds=10.0,
        burst=1,
        max_concurrency=1,
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
    )
    first = limiter.acquire("worker-1", rule=rule)
    if first.allowed:
        limiter.release("worker-1", rule=rule)
    second = limiter.acquire("worker-1", rule=rule)
    print(f"  First allowed={first.allowed}, second allowed={second.allowed}")


async def main() -> None:
    """Run all offline rate-limiting demonstrations."""
    configure_telemetry(mode="off", service_name="demo-rate-limiting")
    agent = CustomerSupplyChainAgent()
    try:
        await _run_tenant_burst(agent)
        await _run_tenant_isolation(agent)
        await _run_explicit_async_limiter()
        _run_explicit_sync_limiter()
        print("\nRate limiting demo completed successfully.")
    finally:
        reset_telemetry()


if __name__ == "__main__":
    asyncio.run(main())
