# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Domain-agnostic metric predicates for CI/CD evaluation quality gates."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import dataclasses
import operator
from typing import Literal

ComparisonOp = Literal["le", "lt", "ge", "gt", "eq"]

_OPERATORS: dict[ComparisonOp, Callable[[float, float], bool]] = {
    "le": operator.le,
    "lt": operator.lt,
    "ge": operator.ge,
    "gt": operator.gt,
    "eq": operator.eq,
}


@dataclasses.dataclass(frozen=True, slots=True)
class MetricThresholdPredicate:
    """Evaluates whether a named metric satisfies a threshold comparison."""

    metric_name: str
    threshold: float
    comparison: ComparisonOp = "ge"

    def evaluate(self, metrics: Mapping[str, float]) -> bool:
        """Evaluates the predicate; raises KeyError if metric is missing."""
        if self.metric_name not in metrics:
            raise KeyError(
                f"Required gate metric {self.metric_name!r} missing from "
                f"scores: {sorted(metrics.keys())}"
            )
        actual = float(metrics[self.metric_name])
        return bool(_OPERATORS[self.comparison](actual, self.threshold))
