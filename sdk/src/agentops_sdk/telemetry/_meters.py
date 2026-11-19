# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Thread-safe OpenTelemetry meter and instrument cache."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import threading
from typing import Any, TypeAlias

from opentelemetry import metrics

_AttrMap: TypeAlias = Mapping[str, str | int | float | bool]


class CachedMeterTracker:
    """Thread-safe cache for OpenTelemetry counter and histogram instruments."""

    def __init__(
        self,
        meter: metrics.Meter | None = None,
        meter_name: str = "agentops_sdk",
    ) -> None:
        self._explicit_meter, self._meter_name = meter, meter_name
        self._lock = threading.Lock()
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}
        self._up_down_counters: dict[str, Any] = {}

    def _get(
        self, cache: dict[str, Any], name: str, factory: Callable[[str], Any]
    ) -> Any:
        with self._lock:
            if name not in cache:
                cache[name] = factory(name)
            return cache[name]

    def _meter(self) -> metrics.Meter:
        return self._explicit_meter or metrics.get_meter(self._meter_name)

    def add_counter(
        self,
        name: str,
        amount: int | float = 1,
        attributes: _AttrMap | None = None,
    ) -> None:
        """Increments a cached monotonic Counter instrument."""
        inst = self._get(self._counters, name, self._meter().create_counter)
        inst.add(amount, attributes=dict(attributes) if attributes else {})

    def record_histogram(
        self, name: str, value: int | float, attributes: _AttrMap | None = None
    ) -> None:
        """Records a measurement on a cached Histogram instrument."""
        inst = self._get(self._histograms, name, self._meter().create_histogram)
        inst.record(value, attributes=dict(attributes) if attributes else {})

    def add_up_down_counter(
        self, name: str, amount: int | float, attributes: _AttrMap | None = None
    ) -> None:
        """Adjusts a cached UpDownCounter instrument."""
        fn = self._meter().create_up_down_counter
        inst = self._get(self._up_down_counters, name, fn)
        inst.add(amount, attributes=dict(attributes) if attributes else {})

    def clear(self) -> None:
        """Clears cached instruments when MeterProvider is reconfigured."""
        with self._lock:
            self._counters.clear()
            self._histograms.clear()
            self._up_down_counters.clear()


_METER_TRACKER = CachedMeterTracker()


def get_meter_tracker() -> CachedMeterTracker:
    """Returns the process-wide thread-safe CachedMeterTracker."""
    return _METER_TRACKER


def record_fallback_metric(component: str, reason: str) -> None:
    """Records a degraded-mode or fallback event counter."""
    _METER_TRACKER.add_counter(
        "agentops.sdk.fallback_count",
        1,
        attributes={"component": component, "reason": reason},
    )
