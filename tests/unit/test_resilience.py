# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for agentops_sdk.resilience rate limiting and validation."""

from __future__ import annotations

import asyncio
import concurrent.futures

from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
import pydantic
import pytest

from agentops_sdk.config import settings
from agentops_sdk.domain import context
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import models
from agentops_sdk.resilience import AsyncRateLimiter
from agentops_sdk.resilience import SyncRateLimiter
from agentops_sdk.resilience import rate_limit
from agentops_sdk.resilience import validate_tool_output


class _ItemOut(pydantic.BaseModel):
    part_id: str
    qty: int = pydantic.Field(ge=1)


def test_sync_rate_limiter_algorithms_and_rules() -> None:
    limiter = SyncRateLimiter()
    sw_rule = models.RateLimitRule(
        "sw",
        rate=2,
        period_seconds=10.0,
        burst=2,
        algorithm=models.RateLimitAlgorithm.SLIDING_WINDOW,
    )
    limiter.register_rule("tenant:", sw_rule)
    assert limiter.acquire("tenant:a").allowed is True
    assert limiter.acquire("tenant:a").allowed is True
    denied = limiter.acquire("tenant:a")
    assert denied.allowed is False and denied.retry_after_seconds > 0.0
    limiter.release("tenant:a")

    conc_rule = models.RateLimitRule(
        "conc",
        max_concurrency=1,
        algorithm=models.RateLimitAlgorithm.CONCURRENCY_SEMAPHORE,
    )
    assert limiter.acquire("k1", rule=conc_rule).allowed is True
    assert limiter.acquire("k1", rule=conc_rule).allowed is False
    limiter.release("k1", rule=conc_rule)
    assert limiter.acquire("k1", rule=conc_rule).allowed is True
    limiter.reset()


async def test_rate_limit_decorator_sync_async_and_tenant_keys() -> None:
    s_lim = SyncRateLimiter()

    @rate_limit(rule_id="sync_op", rate=1, burst=1, limiter=s_lim)
    def sync_op(tenant_id: str = "x") -> str:
        return tenant_id

    with context.bind_execution_scope(claims={"tenant_id": "t-1"}):
        assert sync_op() == "x"
        with pytest.raises(exceptions.RateLimitExceededError):
            sync_op()
    with context.bind_execution_scope(claims={"tenant_id": "t-2"}):
        assert sync_op() == "x"

    a_lim = AsyncRateLimiter()
    with pytest.raises(TypeError):
        rate_limit(limiter=a_lim)(lambda: "bad")

    @rate_limit(
        rule_id="async_op",
        rate=1,
        burst=1,
        max_concurrency=1,
        limiter=a_lim,
        key_func=lambda user: f"k:{user}",
    )
    async def async_op(user: str) -> str:
        return user

    assert await async_op("u1") == "u1"
    with pytest.raises(exceptions.RateLimitExceededError) as exc_info:
        await async_op("u1")
    assert exc_info.value.status_code == 429


async def test_async_rate_limiter_multi_thread_loops_and_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = models.RateLimitRule("mt", rate=10, burst=10, max_concurrency=10)
    limiter = AsyncRateLimiter(rule)

    def _run_in_new_loop(worker_id: int) -> list[bool]:
        async def _inner() -> list[bool]:
            out: list[bool] = []
            for _ in range(3):
                res = await limiter.acquire(f"w:{worker_id}")
                out.append(res.allowed)
                await limiter.release(f"w:{worker_id}")
            return out

        return asyncio.run(_inner())

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(_run_in_new_loop, range(3)))
    assert all(all(item) for item in results)

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    settings.reset_settings_cache()
    tiny = models.RateLimitRule("tiny", rate=1, burst=1, max_concurrency=1)
    sync_lim = SyncRateLimiter(tiny)
    assert sync_lim.acquire("k").allowed is True
    assert sync_lim.acquire("k").allowed is True
    assert (await limiter.acquire("k", rule=tiny)).allowed is True
    assert (await limiter.acquire("k", rule=tiny)).allowed is True


def test_validate_tool_output_sync_retries_and_list(
    otel_span_exporter: InMemorySpanExporter,
) -> None:
    attempts = 0

    @validate_tool_output(
        schema_model=_ItemOut,
        assertion_callback=lambda out: out["qty"] >= 2,
        max_retries=3,
        retry_delay_seconds=0.001,
        retry_on_exceptions=(RuntimeError,),
    )
    def flaky_tool() -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient")
        if attempts == 2:
            return {"part_id": "P-1", "qty": 1}
        return {"part_id": "P-1", "qty": 5}

    with trace.get_tracer("test").start_as_current_span("tool"):
        assert flaky_tool() == {"part_id": "P-1", "qty": 5}
    assert attempts == 3

    @validate_tool_output(schema_model=_ItemOut, max_retries=1)
    def list_tool() -> str:
        return '[{"part_id": "P-2", "qty": 2}]'

    assert "P-2" in list_tool()
    spans = otel_span_exporter.get_finished_spans()
    assert spans[0].attributes["tool.validation.status"] == "PASS"


async def test_validate_tool_output_async_exhaustion_and_spans(
    otel_span_exporter: InMemorySpanExporter,
) -> None:
    @validate_tool_output(
        assertion_callback=lambda _x: False,
        max_retries=2,
        jitter_seconds=0.001,
    )
    async def always_bad() -> str:
        return "bad"

    with trace.get_tracer("test").start_as_current_span("bad_tool"):
        with pytest.raises(exceptions.ToolValidationError) as err:
            await always_bad()
        assert err.value.attempts == 2

    spans = otel_span_exporter.get_finished_spans()
    assert spans[0].attributes["tool.validation.status"] == "FAIL"
