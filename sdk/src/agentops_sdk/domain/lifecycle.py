# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Shared sync/async decorator shell factory and bounded TTL LRU cache."""

from __future__ import annotations

import collections
from collections.abc import Callable
import functools
import inspect
import random
import threading
import time
from typing import Any, Generic, ParamSpec, TypeVar

_P = ParamSpec("_P")
_R = TypeVar("_R")
_K = TypeVar("_K")
_V = TypeVar("_V")
_S = TypeVar("_S")


def _make_async_wrapper(
    func: Callable[_P, Any],
    *,
    on_enter: Callable[
        [tuple[Any, ...], dict[str, Any]],
        tuple[_S, tuple[Any, ...], dict[str, Any]],
    ],
    on_success: Callable[[_S, Any], Any] | None,
    on_error: Callable[[_S, BaseException], None] | None,
    on_finally: Callable[[_S], None] | None,
) -> Callable[_P, Any]:
    @functools.wraps(func)
    async def async_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> Any:
        state, call_args, call_kwargs = on_enter(args, kwargs)
        try:
            result = await func(*call_args, **call_kwargs)
            return on_success(state, result) if on_success else result
        except BaseException as exc:
            if on_error is not None:
                on_error(state, exc)
            raise
        finally:
            if on_finally is not None:
                on_finally(state)

    return async_wrapper


def bind_sync_or_async(
    func: Callable[_P, _R],
    *,
    on_enter: Callable[
        [tuple[Any, ...], dict[str, Any]],
        tuple[_S, tuple[Any, ...], dict[str, Any]],
    ],
    on_success: Callable[[_S, Any], Any] | None = None,
    on_error: Callable[[_S, BaseException], None] | None = None,
    on_finally: Callable[[_S], None] | None = None,
) -> Callable[_P, _R]:
    """Wraps a sync or async callable without duplicating lifecycle logic."""
    if inspect.iscoroutinefunction(func):
        return _make_async_wrapper(
            func,
            on_enter=on_enter,
            on_success=on_success,
            on_error=on_error,
            on_finally=on_finally,
        )

    @functools.wraps(func)
    def sync_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> Any:
        state, call_args, call_kwargs = on_enter(args, kwargs)
        try:
            result = func(*call_args, **call_kwargs)
            return on_success(state, result) if on_success else result
        except BaseException as exc:
            if on_error is not None:
                on_error(state, exc)
            raise
        finally:
            if on_finally is not None:
                on_finally(state)

    return sync_wrapper


class BoundedTTLCache(Generic[_K, _V]):
    """Thread-safe bounded LRU cache with monotonic TTL and jitter."""

    def __init__(
        self,
        maxsize: int = 1024,
        ttl_seconds: float = 300.0,
        jitter_ratio: float = 0.1,
    ) -> None:
        """Initializes BoundedTTLCache with maxsize, TTL, and jitter."""
        if (
            isinstance(maxsize, bool)
            or isinstance(ttl_seconds, bool)
            or not isinstance(maxsize, int)
            or maxsize <= 0
            or ttl_seconds <= 0
        ):
            raise ValueError(
                "maxsize must be a positive int and ttl_seconds positive."
            )
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        self._jitter = max(0.0, min(jitter_ratio, 0.5))
        self._lock = threading.Lock()
        self._entries: collections.OrderedDict[_K, tuple[_V, float]] = (
            collections.OrderedDict()
        )

    def get(self, key: _K) -> _V | None:
        """Returns cached value if present and unexpired, else None."""
        now = time.monotonic()
        with self._lock:
            item = self._entries.get(key)
            if item is None:
                return None
            value, expires_at = item
            if now >= expires_at:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def set(
        self, key: _K, value: _V, ttl_override: float | None = None
    ) -> None:
        """Stores a value under key with bounded LRU eviction."""
        base_ttl = ttl_override if ttl_override is not None else self._ttl
        factor = 1.0 + random.uniform(-self._jitter, self._jitter)  # noqa: S311
        expires_at = time.monotonic() + max(0.01, base_ttl * factor)
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
            self._entries[key] = (value, expires_at)
            while len(self._entries) > self._maxsize:
                self._entries.popitem(last=False)

    def pop(self, key: _K) -> _V | None:
        """Removes and returns key if present."""
        with self._lock:
            item = self._entries.pop(key, None)
            return item[0] if item is not None else None

    def clear(self) -> None:
        """Clears all cached entries."""
        with self._lock:
            self._entries.clear()
