# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Port contracts for agent response and trajectory evaluators."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EvaluatorPort(Protocol):
    """Port contract for pointwise, pairwise, and trajectory evaluation."""

    def evaluate(
        self,
        dataset: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        """Evaluates dataset instances and returns aggregate metric scores."""
        ...

    def evaluate_trajectory(
        self,
        trajectory: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        """Evaluates a multi-turn agent trajectory and returns metric scores."""
        ...

    async def aevaluate(
        self,
        dataset: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        """Asynchronously evaluates dataset instances."""
        ...


@runtime_checkable
class ExperimentTrackerPort(Protocol):
    """Port contract for logging experiment run parameters and metrics."""

    def log_run(
        self,
        experiment_name: str,
        run_name: str,
        params: Mapping[str, Any],
        metrics: Mapping[str, float | int | str],
    ) -> None:
        """Logs parameters and metrics for a single experiment run."""
        ...
