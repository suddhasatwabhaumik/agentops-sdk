# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Evaluation framework, Vertex AI adapters, quality gates, and reporting."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentops_sdk.evaluation import cloud_models as cloud_models
    from agentops_sdk.evaluation import code_validator as code_validator
    from agentops_sdk.evaluation import experiments as experiments
    from agentops_sdk.evaluation import genai_evals as genai_evals
    from agentops_sdk.evaluation import heuristics as heuristics
    from agentops_sdk.evaluation import in_memory as in_memory
    from agentops_sdk.evaluation import metric_registry as metric_registry
    from agentops_sdk.evaluation import models as models
    from agentops_sdk.evaluation import quality_gates as quality_gates
    from agentops_sdk.evaluation import ragas_adapter as ragas_adapter
    from agentops_sdk.evaluation import registry as registry
    from agentops_sdk.evaluation import reporting as reporting
    from agentops_sdk.evaluation import simulator as simulator
    from agentops_sdk.evaluation import suite_builder as suite_builder
    from agentops_sdk.evaluation import vertex_task as vertex_task

_SUBMODULES = frozenset(
    {
        "cloud_models",
        "code_validator",
        "experiments",
        "genai_evals",
        "heuristics",
        "in_memory",
        "metric_registry",
        "models",
        "quality_gates",
        "ragas_adapter",
        "registry",
        "reporting",
        "simulator",
        "suite_builder",
        "vertex_task",
    }
)

_GROUPS: tuple[tuple[str, str], ...] = (
    (
        "agentops_sdk.evaluation.models",
        "CustomRubricMetric EvaluationResult EvaluationSuiteResult "
        "InstanceEvaluation MetricScore VertexAIEvalConfig",
    ),
    (
        "agentops_sdk.evaluation.cloud_models",
        "AutoLossAnalysisResult CloudEvaluationRunResult LossCluster "
        "RegisteredMetricInfo SimulationTrajectory UserScenario "
        "UserScenarioGenerationConfig UserSimulatorConfig",
    ),
    ("agentops_sdk.evaluation.code_validator", "validate_metric_code"),
    (
        "agentops_sdk.evaluation.heuristics",
        "CompositeEvaluator HeuristicEvaluator trajectory_to_item",
    ),
    (
        "agentops_sdk.evaluation.in_memory",
        "EvaluatorRegistry InMemoryEvaluator evaluator_registry get_evaluator",
    ),
    ("agentops_sdk.evaluation.ragas_adapter", "RagasEvaluator"),
    ("agentops_sdk.evaluation.vertex_task", "VertexEvalTaskAdapter"),
    (
        "agentops_sdk.evaluation.reporting",
        "QualityGateSessionReporter QualityGateTestRecord "
        "build_suite_result get_session_reporter session_reporter "
        "use_session_reporter",
    ),
    (
        "agentops_sdk.evaluation.quality_gates",
        "assert_quality_gate assert_trajectory_quality_gate "
        "evaluate_agent_response evaluate_agent_trajectory",
    ),
    (
        "agentops_sdk.evaluation.experiments",
        "ExperimentConfig ExperimentEngine LocalExperimentTracker",
    ),
    (
        "agentops_sdk.evaluation.simulator",
        "generate_conversation_scenarios normalize_agent_info "
        "simulate_agent_conversations",
    ),
    (
        "agentops_sdk.evaluation.metric_registry",
        "clear_metric_registry_cache delete_registered_metric "
        "get_registered_metric list_registered_metrics "
        "register_code_execution_metric register_llm_metric",
    ),
    (
        "agentops_sdk.evaluation.genai_evals",
        "evaluate_agent_engine generate_loss_clusters",
    ),
)


def _build_export_map() -> dict[str, tuple[str, str]]:
    mapping: dict[str, tuple[str, str]] = {}
    for mod_name, raw_syms in _GROUPS:
        for sym in raw_syms.split():
            mapping[sym] = (mod_name, sym)
    return mapping


_EXPORT_MAP = _build_export_map()
__all__ = sorted({*_SUBMODULES, *_EXPORT_MAP})


def __getattr__(name: str) -> Any:
    if name in _SUBMODULES:
        mod = importlib.import_module(f"agentops_sdk.evaluation.{name}")
        globals()[name] = mod
        return mod
    if target := _EXPORT_MAP.get(name):
        mod_name, attr_name = target
        val = getattr(importlib.import_module(mod_name), attr_name)
        globals()[name] = val
        return val
    raise AttributeError(
        f"module 'agentops_sdk.evaluation' has no attribute {name!r}"
    )


def __dir__() -> list[str]:
    return sorted({*_SUBMODULES, *_EXPORT_MAP})
