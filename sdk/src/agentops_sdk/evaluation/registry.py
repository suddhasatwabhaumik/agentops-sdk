# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Composite evaluator and thread-safe lazy EvaluatorRegistry."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
import importlib
import threading
from typing import Any, TypeAlias

from typing_extensions import override

from agentops_sdk.ports import evaluation as eval_ports

_EvaluatorFactory: TypeAlias = (
    Callable[..., eval_ports.EvaluatorPort] | eval_ports.EvaluatorPort
)


def _create_in_memory_evaluator(**kwargs: Any) -> eval_ports.EvaluatorPort:
    mod = importlib.import_module("agentops_sdk.evaluation.in_memory")
    cls: Callable[..., eval_ports.EvaluatorPort] = mod.InMemoryEvaluator
    return cls(**kwargs)


def _create_heuristic_evaluator(**kwargs: Any) -> eval_ports.EvaluatorPort:
    mod = importlib.import_module("agentops_sdk.evaluation.heuristics")
    cls: Callable[..., eval_ports.EvaluatorPort] = mod.HeuristicEvaluator
    return cls(**kwargs)


def _create_ragas_evaluator(**kwargs: Any) -> eval_ports.EvaluatorPort:
    mod = importlib.import_module("agentops_sdk.evaluation.ragas_adapter")
    cls: Callable[..., eval_ports.EvaluatorPort] = mod.RagasEvaluator
    return cls(**kwargs)


def _create_vertex_evaluator(**kwargs: Any) -> eval_ports.EvaluatorPort:
    mod = importlib.import_module("agentops_sdk.evaluation.vertex_task")
    cls: Callable[..., eval_ports.EvaluatorPort] = mod.VertexEvalTaskAdapter
    return cls(**kwargs)


class CompositeEvaluator(eval_ports.EvaluatorPort):
    """Combines multiple EvaluatorPort instances into one evaluation pass."""

    def __init__(
        self,
        evaluators: Sequence[eval_ports.EvaluatorPort],
        *,
        prefix_provider: bool = False,
    ) -> None:
        if not evaluators:
            raise ValueError("evaluators must not be empty.")
        self.evaluators = list(evaluators)
        self.prefix_provider = prefix_provider
        self.provider_name = "composite"

    def _prefix_for(self, evaluator: eval_ports.EvaluatorPort) -> str:
        if not self.prefix_provider:
            return ""
        provider_name = getattr(
            evaluator, "provider_name", type(evaluator).__name__
        )
        return f"{provider_name}/"

    def _merge_scores(
        self, results: Sequence[Mapping[str, float]]
    ) -> dict[str, float]:
        merged: dict[str, float] = {}
        for evaluator, scores in zip(self.evaluators, results, strict=True):
            prefix = self._prefix_for(evaluator)
            for key, value in scores.items():
                merged[f"{prefix}{key}"] = float(value)
        return merged

    @override
    def evaluate(
        self,
        dataset: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        results = [
            ev.evaluate(dataset, metrics=metrics) for ev in self.evaluators
        ]
        return self._merge_scores(results)

    @override
    def evaluate_trajectory(
        self,
        trajectory: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        results = [
            ev.evaluate_trajectory(trajectory, metrics=metrics)
            for ev in self.evaluators
        ]
        return self._merge_scores(results)

    @override
    async def aevaluate(
        self,
        dataset: Sequence[Mapping[str, Any]],
        *,
        metrics: Sequence[str] | None = None,
    ) -> dict[str, float]:
        results = await asyncio.gather(
            *(ev.aevaluate(dataset, metrics=metrics) for ev in self.evaluators)
        )
        return self._merge_scores(results)


def _default_providers() -> dict[str, _EvaluatorFactory]:
    return {
        "in_memory": _create_in_memory_evaluator,
        "heuristic": _create_heuristic_evaluator,
        "ragas": _create_ragas_evaluator,
        "vertex": _create_vertex_evaluator,
        "vertex_ai": _create_vertex_evaluator,
        "composite": CompositeEvaluator,
    }


class EvaluatorRegistry:
    """Thread-safe registry and factory for EvaluatorPort implementations."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._providers: dict[str, _EvaluatorFactory] = _default_providers()

    def register(self, name: str, provider: _EvaluatorFactory) -> None:
        """Registers an evaluator factory or instance under name."""
        clean = name.strip().lower()
        if not clean:
            raise ValueError("Evaluator name must not be empty.")
        with self._lock:
            self._providers[clean] = provider

    def has(self, name: str) -> bool:
        """Returns True if an evaluator provider is registered under name."""
        with self._lock:
            return name.strip().lower() in self._providers

    def list_evaluators(self) -> list[str]:
        """Returns sorted registered evaluator provider names."""
        with self._lock:
            return sorted(self._providers)

    def clear(self) -> None:
        """Removes all registered evaluator providers."""
        with self._lock:
            self._providers.clear()

    def reset(self) -> None:
        """Restores built-in evaluator providers and drops custom entries."""
        with self._lock:
            self._providers = _default_providers()

    def get(
        self,
        provider: str
        | eval_ports.EvaluatorPort
        | Sequence[str | eval_ports.EvaluatorPort],
        **kwargs: Any,
    ) -> eval_ports.EvaluatorPort:
        """Resolves a provider string, sequence, or EvaluatorPort instance."""
        if isinstance(provider, eval_ports.EvaluatorPort):
            return provider
        if isinstance(provider, Sequence) and not isinstance(
            provider, str | bytes
        ):
            parts = [
                self.get(item) if isinstance(item, str) else item
                for item in provider
            ]
            return CompositeEvaluator(parts, **kwargs)
        if not isinstance(provider, str):
            raise ValueError(f"Unsupported evaluator specifier: {provider!r}")
        with self._lock:
            target = self._providers.get(provider.strip().lower())
        if target is None:
            raise ValueError(f"Unknown evaluator mode: {provider!r}")
        return target(**kwargs) if callable(target) else target


evaluator_registry = EvaluatorRegistry()


def get_evaluator(
    name: str
    | eval_ports.EvaluatorPort
    | Sequence[str | eval_ports.EvaluatorPort],
    **kwargs: Any,
) -> eval_ports.EvaluatorPort:
    """Instantiates an EvaluatorPort by registered mode name or sequence."""
    return evaluator_registry.get(name, **kwargs)
