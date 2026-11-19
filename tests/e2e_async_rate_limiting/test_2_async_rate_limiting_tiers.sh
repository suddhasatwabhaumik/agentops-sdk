#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 2: Multi-Tier Async Rate Limiting, Algorithms & Tenant Isolation (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

echo "[TEST 2 INFO] Running Multi-Tier Async Rate Limiting & Isolation Tests..."

"${PYTHON_BIN}" - << 'PYEOF'
import asyncio

from agentops_sdk import AsyncRateLimiter
from agentops_sdk import RateLimitAlgorithm
from agentops_sdk import RateLimitExceededError
from agentops_sdk import RateLimitRule
from agentops_sdk import SyncRateLimiter
from agentops_sdk import bind_execution_scope
from agentops_sdk import configure_telemetry
from agentops_sdk import rate_limit
from agentops_sdk import reset_telemetry


async def run_checks() -> None:
    configure_telemetry(mode="off", service_name="e2e-rl-tiers")
    try:
        limiter = AsyncRateLimiter()
        rule_a = RateLimitRule(
            rule_id="strict-a",
            rate=2,
            period_seconds=60.0,
            burst=2,
            algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        )
        rule_b = RateLimitRule(
            rule_id="generous-b",
            rate=5,
            period_seconds=60.0,
            burst=5,
            algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        )
        limiter.register_rule("tenant:enterprise-a", rule_a)
        limiter.register_rule("tenant:enterprise-b", rule_b)

        print("[2.1] Verifying Sliding Window quota + exhaustion...")
        for _ in range(2):
            res = await limiter.acquire("tenant:enterprise-a")
            assert res.allowed is True
            assert res.remaining >= 0
        blocked = await limiter.acquire("tenant:enterprise-a")
        assert blocked.allowed is False
        assert blocked.retry_after_seconds > 0

        print("[2.2] Verifying tenant B isolation...")
        for _ in range(3):
            res_b = await limiter.acquire("tenant:enterprise-b")
            assert res_b.allowed is True

        print("[2.3] Verifying @rate_limit decorator HTTP 429 & ExecutionScope...")
        dec_limiter = AsyncRateLimiter()

        @rate_limit(
            rule_id="decorator-tier",
            rate=2,
            period_seconds=60.0,
            burst=2,
            limiter=dec_limiter,
            algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
        )
        async def protected_tool() -> str:
            return "ok"

        with bind_execution_scope(
            user_id="u-a", claims={"tenant_id": "dec-tenant-a"}
        ):
            assert await protected_tool() == "ok"
            assert await protected_tool() == "ok"
            try:
                await protected_tool()
                raise AssertionError("Expected RateLimitExceededError")
            except RateLimitExceededError as exc:
                assert exc.status_code == 429
                assert exc.retry_after_seconds > 0

        with bind_execution_scope(
            user_id="u-b", claims={"tenant_id": "dec-tenant-b"}
        ):
            assert await protected_tool() == "ok"

        print("[2.4] Verifying Token Bucket temporal refill...")
        tb_limiter = AsyncRateLimiter(
            default_rule=RateLimitRule(
                rule_id="tb-refill",
                rate=10,
                period_seconds=1.0,
                burst=3,
                algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
            )
        )
        for _ in range(3):
            assert (await tb_limiter.acquire("tb")).allowed is True
        assert (await tb_limiter.acquire("tb")).allowed is False
        await asyncio.sleep(0.25)
        assert (await tb_limiter.acquire("tb")).allowed is True

        sync_limiter = SyncRateLimiter(
            default_rule=RateLimitRule(rule_id="sync", rate=1, period_seconds=10.0, burst=1)
        )
        assert sync_limiter.acquire("worker").allowed is True
        assert sync_limiter.acquire("worker").allowed is False
    finally:
        reset_telemetry()


asyncio.run(run_checks())
print("ALL MULTI-TIER RATE LIMITING CHECKS PASSED.")
PYEOF

echo "[TEST 2 PASS] Multi-tier rate limiting, token buckets, and isolation verified."
