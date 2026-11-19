# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Thread-safe SyncRateLimiter, loop-safe AsyncRateLimiter, and @rate_limit."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import dataclasses
import functools
import inspect
import time
from typing import Any, NoReturn, ParamSpec, TypeVar
import weakref

from agentops_sdk.config import settings
from agentops_sdk.domain import context
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import models
from agentops_sdk.resilience import _rate_core

_P = ParamSpec("_P")
_R = TypeVar("_R")


@dataclasses.dataclass(frozen=True, slots=True)
class _LimitPolicy:
    rule: models.RateLimitRule
    key_func: Callable[..., str] | None
    limiter: SyncRateLimiter | AsyncRateLimiter | None
    trust_unverified_kwargs: bool


class SyncRateLimiter(_rate_core.RateLimiterCore):
    """Thread-safe rate limiter that sleeps strictly outside its lock."""

    def acquire(
        self, key: str = "global", *, rule: models.RateLimitRule | None = None
    ) -> models.RateLimitResult:
        """Acquires a rate-limit and concurrency slot synchronously."""
        eff = self._resolve_rule(key, rule)
        if not settings.get_rate_limit_settings().enabled:
            return models.RateLimitResult(
                True, key, float(max(1, eff.burst)), 0.0
            )
        deadline = time.monotonic() + max(0.0, eff.wait_timeout_seconds)
        while True:
            res = self._step(eff, key)
            if res.allowed or (rem := deadline - time.monotonic()) <= 0.0:
                _rate_core.record_decision(res.allowed)
                return res
            time.sleep(min(res.retry_after_seconds, rem))

    def release(
        self, key: str = "global", *, rule: models.RateLimitRule | None = None
    ) -> None:
        """Releases an active concurrency slot and decrements OTel gauge."""
        if self._dec_in_flight(self._resolve_rule(key, rule), key):
            _rate_core.release_concurrency_metric()


class AsyncRateLimiter(_rate_core.RateLimiterCore):
    """Coroutine-safe rate limiter isolating asyncio.Lock per event loop."""

    def __init__(
        self, default_rule: models.RateLimitRule | None = None
    ) -> None:
        super().__init__(default_rule)
        self._loop_locks: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Lock
        ] = weakref.WeakKeyDictionary()

    def _get_loop_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        with self._meta_lock:
            return self._loop_locks.setdefault(loop, asyncio.Lock())

    async def acquire(
        self, key: str = "global", *, rule: models.RateLimitRule | None = None
    ) -> models.RateLimitResult:
        """Acquires a rate-limit and concurrency slot asynchronously."""
        eff = self._resolve_rule(key, rule)
        if not settings.get_rate_limit_settings().enabled:
            return models.RateLimitResult(
                True, key, float(max(1, eff.burst)), 0.0
            )
        deadline = time.monotonic() + max(0.0, eff.wait_timeout_seconds)
        loop_lock = self._get_loop_lock()
        while True:
            async with loop_lock:
                res = self._step(eff, key)
            if res.allowed or (rem := deadline - time.monotonic()) <= 0.0:
                _rate_core.record_decision(res.allowed)
                return res
            await asyncio.sleep(min(res.retry_after_seconds, rem))

    async def release(
        self, key: str = "global", *, rule: models.RateLimitRule | None = None
    ) -> None:
        """Releases an active concurrency slot and decrements OTel gauge."""
        eff = self._resolve_rule(key, rule)
        async with self._get_loop_lock():
            decremented = self._dec_in_flight(eff, key)
        if decremented:
            _rate_core.release_concurrency_metric()


def _resolve_scope_key(
    args: tuple[Any, ...], kwargs: Mapping[str, Any], pol: _LimitPolicy
) -> str:
    if pol.key_func is not None:
        return str(pol.key_func(*args, **kwargs)).strip() or "global"
    scope = context.current_execution_scope()
    if scope.claims:
        tid = scope.claims.get("tenant_id") or scope.claims.get("tid")
        if tid is not None and str(tid).strip():
            return f"tenant:{str(tid).strip()}"
    if scope.user_id and scope.user_id.strip():
        return f"user:{scope.user_id.strip()}"
    kw_tid = str(kwargs.get("tenant_id") or "").strip()
    return (
        f"tenant:{kw_tid}"
        if pol.trust_unverified_kwargs and kw_tid
        else "global"
    )


def _raise_exceeded(
    key: str, res: models.RateLimitResult, rid: str
) -> NoReturn:
    raise exceptions.RateLimitExceededError(
        f"Rate limit exceeded for {rid!r} ({key!r}).",
        key=key,
        retry_after_seconds=res.retry_after_seconds,
    )


global_rate_limiter = AsyncRateLimiter()
global_sync_rate_limiter = SyncRateLimiter()


def rate_limit(
    *,
    rule_id: str | None = None,
    rate: int = 60,
    period_seconds: float = 60.0,
    burst: int | None = None,
    max_concurrency: int = 25,
    wait_timeout_seconds: float = 0.0,
    algorithm: models.RateLimitAlgorithm = (
        models.RateLimitAlgorithm.TOKEN_BUCKET
    ),
    key_func: Callable[..., str] | None = None,
    limiter: SyncRateLimiter | AsyncRateLimiter | None = None,
    trust_unverified_kwargs: bool = False,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Enforces per-rule and per-tenant rate and concurrency limits."""

    def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
        rid = rule_id or f"{func.__module__}.{func.__qualname__}"
        rule = models.RateLimitRule(
            rid,
            rate,
            period_seconds,
            rate if burst is None else burst,
            max_concurrency,
            wait_timeout_seconds,
            algorithm,
        )
        pol = _LimitPolicy(rule, key_func, limiter, trust_unverified_kwargs)
        if inspect.iscoroutinefunction(func):
            return _wrap_async(func, pol)
        return _wrap_sync(func, pol)

    return decorator


def _wrap_sync(func: Callable[_P, _R], pol: _LimitPolicy) -> Callable[_P, _R]:
    if isinstance(pol.limiter, AsyncRateLimiter):
        raise TypeError("Use SyncRateLimiter for synchronous callables.")
    active = pol.limiter or global_sync_rate_limiter

    @functools.wraps(func)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        key = _resolve_scope_key(args, kwargs, pol)
        res = active.acquire(key, rule=pol.rule)
        if not res.allowed:
            _raise_exceeded(key, res, pol.rule.rule_id)
        try:
            return func(*args, **kwargs)
        finally:
            active.release(key, rule=pol.rule)

    return wrapper


def _wrap_async(
    func: Callable[_P, Any], pol: _LimitPolicy
) -> Callable[_P, Any]:
    active: SyncRateLimiter | AsyncRateLimiter = (
        pol.limiter or global_rate_limiter
    )

    @functools.wraps(func)
    async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> Any:
        key = _resolve_scope_key(args, kwargs, pol)
        if isinstance(active, SyncRateLimiter):
            res = await asyncio.to_thread(active.acquire, key, rule=pol.rule)
        else:
            res = await active.acquire(key, rule=pol.rule)
        if not res.allowed:
            _raise_exceeded(key, res, pol.rule.rule_id)
        try:
            return await func(*args, **kwargs)
        finally:
            if isinstance(active, SyncRateLimiter):
                await asyncio.to_thread(active.release, key, rule=pol.rule)
            else:
                await active.release(key, rule=pol.rule)

    return wrapper
