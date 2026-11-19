# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Internal bucket evaluation, TTL store, and OTel metrics for rate limiting."""

from __future__ import annotations

import collections
import dataclasses
import threading
import time
from typing import TypeAlias

from agentops_sdk.config import settings
from agentops_sdk.domain import lifecycle
from agentops_sdk.domain import models
from agentops_sdk.telemetry import provider

BucketKey: TypeAlias = tuple[str, str]
_PREFIX_DELIMS = frozenset({":", "/", "."})


@dataclasses.dataclass(slots=True)
class BucketState:
    """Mutable token-bucket / sliding-window state guarded by limiter lock."""

    tokens: float
    last_refill: float
    timestamps: collections.deque[float]
    in_flight: int = 0


def _prefix_matches(key: str, pfx: str) -> bool:
    if not pfx or not key.startswith(pfx):
        return False
    if pfx.endswith((":", "/", ".")) or len(key) == len(pfx):
        return True
    return key[len(pfx)] in _PREFIX_DELIMS


def evaluate_bucket(
    state: BucketState, rule: models.RateLimitRule, key: str, now: float
) -> models.RateLimitResult:
    """Evaluates admission against concurrency, sliding-window, or bucket."""
    if rule.max_concurrency > 0 and state.in_flight >= rule.max_concurrency:
        return models.RateLimitResult(False, key, 0.0, 0.05)
    if rule.algorithm == models.RateLimitAlgorithm.CONCURRENCY_SEMAPHORE:
        state.in_flight += 1
        rem = float(rule.max_concurrency - state.in_flight)
        return models.RateLimitResult(True, key, rem, 0.0)
    if rule.algorithm == models.RateLimitAlgorithm.SLIDING_WINDOW:
        cutoff = now - rule.period_seconds
        while state.timestamps and state.timestamps[0] <= cutoff:
            state.timestamps.popleft()
        if len(state.timestamps) < rule.rate:
            state.timestamps.append(now)
            state.in_flight += 1
            rem = float(rule.rate - len(state.timestamps))
            return models.RateLimitResult(True, key, rem, 0.0)
        wait = max(0.001, state.timestamps[0] + rule.period_seconds - now)
        return models.RateLimitResult(False, key, 0.0, wait)
    cap = float(max(1, rule.burst))
    fill = max(0.0, rule.rate / max(rule.period_seconds, 1e-6))
    elapsed = max(0.0, now - state.last_refill)
    state.tokens = min(cap, state.tokens + elapsed * fill)
    state.last_refill = now
    if state.tokens >= 1.0:
        state.tokens -= 1.0
        state.in_flight += 1
        return models.RateLimitResult(True, key, state.tokens, 0.0)
    wait = (
        max(0.001, rule.period_seconds)
        if fill <= 0.0
        else max(0.001, (1.0 - state.tokens) / fill)
    )
    return models.RateLimitResult(False, key, 0.0, wait)


def record_decision(allowed: bool) -> None:
    """Increments request/rejection and active-concurrency OTel counters."""
    tracker = provider.get_meter_tracker()
    tracker.add_counter(
        "agentops.rate_limit.requests.total", 1, {"allowed": str(allowed)}
    )
    if allowed:
        tracker.add_up_down_counter("agentops.rate_limit.concurrency.active", 1)
    else:
        tracker.add_counter("agentops.rate_limit.rejected.total", 1)


def release_concurrency_metric() -> None:
    """Decrements the active-concurrency OTel gauge."""
    provider.get_meter_tracker().add_up_down_counter(
        "agentops.rate_limit.concurrency.active", -1
    )


class RateLimiterCore:
    """Shared bounded TTL bucket store and prefix rule registry."""

    def __init__(
        self, default_rule: models.RateLimitRule | None = None
    ) -> None:
        self._explicit_default = default_rule
        self._prefix_rules: dict[str, models.RateLimitRule] = {}
        self._meta_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._buckets: lifecycle.BoundedTTLCache[BucketKey, BucketState] = (
            lifecycle.BoundedTTLCache(maxsize=10_000, ttl_seconds=3600.0)
        )

    def _get_default_rule(self) -> models.RateLimitRule:
        if self._explicit_default is not None:
            return self._explicit_default
        cfg = settings.get_rate_limit_settings()
        return models.RateLimitRule(
            "default",
            cfg.requests_per_minute,
            60.0,
            cfg.burst_size,
            cfg.max_concurrency,
            cfg.wait_timeout_seconds,
        )

    def register_rule(
        self, key_prefix: str, rule: models.RateLimitRule
    ) -> None:
        """Registers a delimiter-aware prefix-matched quota rule."""
        with self._meta_lock:
            self._prefix_rules[key_prefix] = rule

    def reset(self) -> None:
        """Clears all active bucket states."""
        with self._state_lock:
            self._buckets.clear()

    def _resolve_rule(
        self, key: str, rule: models.RateLimitRule | None
    ) -> models.RateLimitRule:
        if rule is not None:
            return rule
        with self._meta_lock:
            items = sorted(
                self._prefix_rules.items(),
                key=lambda kv: len(kv[0]),
                reverse=True,
            )
        for pfx, cand in items:
            if _prefix_matches(key, pfx):
                return cand
        return self._get_default_rule()

    def _step(
        self, eff: models.RateLimitRule, key: str
    ) -> models.RateLimitResult:
        ckey: BucketKey = (eff.rule_id, key)
        now = time.monotonic()
        with self._state_lock:
            state = self._buckets.get(ckey) or BucketState(
                float(max(1, eff.burst)), now, collections.deque()
            )
            res = evaluate_bucket(state, eff, key, now)
            self._buckets.set(ckey, state)
            return res

    def _dec_in_flight(self, eff: models.RateLimitRule, key: str) -> bool:
        ckey: BucketKey = (eff.rule_id, key)
        with self._state_lock:
            state = self._buckets.get(ckey)
            if state is not None and state.in_flight > 0:
                state.in_flight -= 1
                return True
            return False
