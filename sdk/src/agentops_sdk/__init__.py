# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Google AgentOps SDK PEP 562 lazy public facade."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

__version__ = "1.0.0"

if TYPE_CHECKING:
    from agentops_sdk import audit as audit
    from agentops_sdk import config as config
    from agentops_sdk import domain as domain
    from agentops_sdk import evaluation as evaluation
    from agentops_sdk import feature_store as feature_store
    from agentops_sdk import memory as memory
    from agentops_sdk import ports as ports
    from agentops_sdk import prompts as prompts
    from agentops_sdk import resilience as resilience
    from agentops_sdk import security as security
    from agentops_sdk import sql as sql
    from agentops_sdk import telemetry as telemetry

_SUBMODULES = frozenset(
    [
        "audit",
        "config",
        "domain",
        "evaluation",
        "feature_store",
        "memory",
        "ports",
        "prompts",
        "resilience",
        "security",
        "sql",
        "telemetry",
    ]
)

_GROUPS: tuple[tuple[str, str], ...] = (
    (
        "agentops_sdk.domain.context",
        "ExecutionScope bind_execution_scope current_execution_scope "
        "identity_claims_ctx identity_token_ctx session_id_ctx "
        "telemetry_context_ctx user_id_ctx",
    ),
    (
        "agentops_sdk.domain.exceptions",
        "AgentOpsError AuditLedgerError AuthenticationError ConfigurationError "
        "EvaluationError FeatureStoreError MemoryBankError PrivacyScrubError "
        "PromptNotFoundError PromptRegistryError QualityGateError "
        "RateLimitExceededError SQLGovernanceError SecretAccessError "
        "SecurityViolationError SessionMemoryError TelemetryExportError "
        "ToolValidationError",
    ),
    ("agentops_sdk.domain.lifecycle", "BoundedTTLCache bind_sync_or_async"),
    (
        "agentops_sdk.domain.models",
        "AuditLogEntry ConversationTurn DecisionResult LLMUsageMetric "
        "MemoryItem RateLimitAlgorithm RateLimitResult RateLimitRule "
        "SQLValidationResult SafetyScanResult SecurityVerdict TelemetryContext "
        "TelemetryMode UseCaseTelemetry sanitize_metric_slug utc_now_iso",
    ),
    (
        "agentops_sdk.domain.predicates",
        "ComparisonOp MetricThresholdPredicate",
    ),
    ("agentops_sdk.ports.audit", "AuditSinkPort"),
    ("agentops_sdk.ports.evaluation", "EvaluatorPort"),
    ("agentops_sdk.ports.feature_store", "EntityKey FeatureOnlineStorePort"),
    ("agentops_sdk.ports.memory", "LongTermMemoryPort SessionMemoryPort"),
    ("agentops_sdk.ports.prompts", "PromptBackendPort PromptTemplate"),
    (
        "agentops_sdk.ports.security",
        "PrivacyScrubberPort SafetyScannerPort SecretStorePort",
    ),
    ("agentops_sdk.ports.telemetry", "CaseTelemetryExporterPort"),
    (
        "agentops_sdk.config.settings",
        "DatabaseSettings GCPSettings PromptSettings RateLimitSettings "
        "SecuritySettings TelemetrySettings get_database_settings "
        "get_gcp_settings get_prompt_settings get_rate_limit_settings "
        "get_security_settings get_telemetry_settings reset_settings_cache "
        "resolve_gcp_project_and_location validate_gcp_identifier",
    ),
    (
        "agentops_sdk.telemetry",
        "BatchedGCSTelemetryExporter BigQueryTelemetryExporter "
        "CachedMeterTracker CloudLoggingTelemetryExporter "
        "CompositeTelemetryExporter InMemoryTelemetryExporter "
        "LocalFileTelemetryExporter OtlpStdlibLogHandler TelemetryHandles "
        "UseCaseTracker configure_telemetry create_gcp_metric_reader "
        "create_gcp_span_processor emit_finops_telemetry extract_llm_usage "
        "get_active_telemetry_mode get_default_exporter get_meter_tracker "
        "log_llm_metrics record_fallback_metric reset_telemetry "
        "resolve_telemetry_mode scrub_sensitive_text set_default_exporter "
        "trace_agent_flow trace_agent_span trace_llm_call trace_use_case",
    ),
    (
        "agentops_sdk.audit",
        "AuditLedger BigQueryAuditAdapter CloudLoggingAuditAdapter "
        "InMemoryAuditAdapter LocalFileAuditAdapter",
    ),
    (
        "agentops_sdk.memory",
        "CloudSQLSessionMemory CompositeMemory InMemoryLongTermMemory "
        "InMemorySessionMemory VertexMemoryBank validate_user_id",
    ),
    (
        "agentops_sdk.evaluation",
        "AutoLossAnalysisResult CloudEvaluationRunResult CompositeEvaluator "
        "CustomRubricMetric EvaluationResult EvaluationSuiteResult "
        "EvaluatorRegistry ExperimentConfig ExperimentEngine "
        "HeuristicEvaluator InMemoryEvaluator InstanceEvaluation "
        "LocalExperimentTracker LossCluster MetricScore "
        "QualityGateSessionReporter QualityGateTestRecord RagasEvaluator "
        "RegisteredMetricInfo SimulationTrajectory UserScenario "
        "UserScenarioGenerationConfig UserSimulatorConfig VertexAIEvalConfig "
        "VertexEvalTaskAdapter assert_quality_gate "
        "assert_trajectory_quality_gate build_suite_result "
        "clear_metric_registry_cache delete_registered_metric "
        "evaluate_agent_engine evaluate_agent_response "
        "evaluate_agent_trajectory evaluator_registry "
        "generate_conversation_scenarios generate_loss_clusters get_evaluator "
        "get_registered_metric get_session_reporter list_registered_metrics "
        "normalize_agent_info register_code_execution_metric "
        "register_llm_metric session_reporter simulate_agent_conversations "
        "trajectory_to_item use_session_reporter validate_metric_code",
    ),
    (
        "agentops_sdk.prompts",
        "GCSPromptAdapter InMemoryPromptAdapter LocalYAMLPromptAdapter "
        "PromptBackendPort PromptRegistry PromptTemplate VertexAIPromptAdapter "
        "sort_versions validate_prompt_id validate_version_id",
    ),
    (
        "agentops_sdk.security",
        "CloudModelArmorScanner GoogleDLPScrubber InMemorySecretStore "
        "JWTAuthEngine LocalRuleSafetyScanner OutputDLPSanitizer "
        "RegexPrivacyScrubber SecretManagerStore SecurityGuard check_input "
        "check_output create_access_token create_refresh_token "
        "decode_jwt_token fetch_secret get_current_identity "
        "get_user_identity_token hash_password inject_secret "
        "pass_user_identity validate_jwks_url verify_password",
    ),
    (
        "agentops_sdk.sql",
        "RowLevelPolicyEngine SQLASTValidator apply_row_level_policy "
        "validate_sql_query validate_sql_query_decorator",
    ),
    (
        "agentops_sdk.resilience",
        "AsyncRateLimiter SyncRateLimiter global_rate_limiter "
        "global_sync_rate_limiter rate_limit validate_tool_output",
    ),
    (
        "agentops_sdk.feature_store",
        "EntityKey FeatureOnlineStorePort FeatureStoreAdminClient "
        "FeatureStoreOnlineClient InMemoryFeatureOnlineStore "
        "extract_feature_value normalize_entity_key",
    ),
)


def _build_export_map() -> dict[str, tuple[str, str]]:
    mapping: dict[str, tuple[str, str]] = {}
    for mod_name, raw_syms in _GROUPS:
        for sym in raw_syms.split():
            mapping[sym] = (mod_name, sym)
    return mapping


_EXPORT_MAP = _build_export_map()
__all__ = sorted({"__version__", *_SUBMODULES, *_EXPORT_MAP})


def __getattr__(name: str) -> Any:
    if name in _SUBMODULES:
        mod = importlib.import_module(f"agentops_sdk.{name}")
        globals()[name] = mod
        return mod
    if target := _EXPORT_MAP.get(name):
        mod_name, attr_name = target
        val = getattr(importlib.import_module(mod_name), attr_name)
        globals()[name] = val
        return val
    raise AttributeError(f"module 'agentops_sdk' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({"__version__", *_SUBMODULES, *_EXPORT_MAP})
