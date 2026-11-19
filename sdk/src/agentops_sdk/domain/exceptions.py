# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Canonical domain exception hierarchy for the AgentOps SDK."""

from __future__ import annotations

from collections.abc import Mapping


class AgentOpsError(Exception):
    """Base exception for all AgentOps SDK domain errors."""


class ConfigurationError(AgentOpsError):
    """Signals invalid runtime settings or environment variables."""


class SecurityViolationError(AgentOpsError):
    """Signals a failure in prompt, context, or response safety checks."""


class AuthenticationError(AgentOpsError):
    """Signals a failure in JWT or identity verification."""


class SecretAccessError(AgentOpsError):
    """Signals a failure in retrieving or storing a secret."""


class SQLGovernanceError(AgentOpsError):
    """Signals a failure in SQL AST validation or Row-Level Security."""


class PrivacyScrubError(AgentOpsError):
    """Signals a failure in PII scrubbing under a fail-closed policy."""


class MemoryBankError(AgentOpsError):
    """Signals a failure in a Vertex AI Memory Bank operation."""


class SessionMemoryError(AgentOpsError):
    """Signals a failure in relational session memory persistence."""


class PromptRegistryError(AgentOpsError):
    """Signals a failure in prompt registry storage or rendering."""


class PromptNotFoundError(PromptRegistryError):
    """Signals a missing prompt ID or version in the registry."""


class EvaluationError(AgentOpsError):
    """Signals a failure in an evaluation run or metric registry call."""


class QualityGateError(EvaluationError):
    """Signals a failure in one or more evaluation quality gate predicates."""

    def __init__(
        self,
        message: str,
        failed_metrics: Mapping[str, tuple[float, float]] | None = None,
        report_markdown: str = "",
    ) -> None:
        super().__init__(message)
        self.failed_metrics = dict(failed_metrics or {})
        self.report_markdown = report_markdown


class RateLimitExceededError(AgentOpsError):
    """Signals a failure when a caller exceeds rate or concurrency quotas."""

    def __init__(
        self,
        message: str,
        *,
        key: str = "global",
        retry_after_seconds: float = 1.0,
        status_code: int = 429,
    ) -> None:
        super().__init__(message)
        self.key = key
        self.retry_after_seconds = retry_after_seconds
        self.status_code = status_code


class ToolValidationError(AgentOpsError):
    """Signals a failure in decorated tool schema or assertion checks."""

    def __init__(
        self,
        message: str,
        *,
        tool_name: str,
        attempts: int,
        last_error: BaseException | str,
    ) -> None:
        super().__init__(message)
        self.tool_name = tool_name
        self.attempts = attempts
        self.last_error = str(last_error)


class FeatureStoreError(AgentOpsError):
    """Signals a failure in Feature Online Store operations or lookups."""


class AuditLedgerError(AgentOpsError):
    """Signals a failure in persisting an audit ledger record."""


class TelemetryExportError(AgentOpsError):
    """Signals a failure in exporting telemetry records to a configured sink."""
