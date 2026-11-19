# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Pure domain data transfer objects and enumerations for AgentOps SDK."""

from __future__ import annotations

import dataclasses
import datetime
import enum
import re
from typing import Any
import uuid

import pydantic

_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def sanitize_metric_slug(value: str | None, fallback: str) -> str:
    """Normalizes a KPI or budget label into a bounded slug."""
    if not value or not value.strip():
        return fallback
    cleaned = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return cleaned[:128] if cleaned else fallback


def utc_now_iso() -> str:
    """Returns the current UTC timestamp in ISO-8601 format."""
    return datetime.datetime.now(datetime.UTC).isoformat()


class SecurityVerdict(enum.StrEnum):
    """Outcome classification for inbound and outbound safety scans."""

    ALLOW = "ALLOW"
    SANITIZED = "SANITIZED"
    BLOCK = "BLOCK"


class TelemetryMode(enum.StrEnum):
    """Active OpenTelemetry and structured logging export routing mode."""

    AUTO = "auto"
    GCP = "gcp"
    OTLP = "otlp"
    HYBRID = "hybrid"
    OFF = "off"


class RateLimitAlgorithm(enum.StrEnum):
    """Supported traffic shaping and concurrency control algorithms."""

    TOKEN_BUCKET = "token_bucket"  # noqa: S105
    SLIDING_WINDOW = "sliding_window"
    CONCURRENCY_SEMAPHORE = "concurrency_semaphore"


class TelemetryContext(pydantic.BaseModel):
    """Business FinOps and multi-agent correlation metadata."""

    model_config = pydantic.ConfigDict(frozen=True, extra="ignore")

    business_kpi: str = "agent-execution"
    pl_impact_line: str = "ai-platform-opex"
    project_name: str = "agentops-service"
    environment: str = "dev"
    session_id: str | None = None
    user_id: str | None = None

    @pydantic.field_validator("business_kpi", mode="before")
    @classmethod
    def _clean_kpi(cls, val: object) -> str:
        return sanitize_metric_slug(
            str(val) if val is not None else None, "agent-execution"
        )

    @pydantic.field_validator("pl_impact_line", mode="before")
    @classmethod
    def _clean_pl(cls, val: object) -> str:
        return sanitize_metric_slug(
            str(val) if val is not None else None, "ai-platform-opex"
        )


class LLMUsageMetric(pydantic.BaseModel):
    """Token consumption and latency telemetry for a single LLM call."""

    model_config = pydantic.ConfigDict(frozen=True)

    model_name: str = "unknown"
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: float = 0.0
    status: str = "OK"
    error_message: str | None = None


class UseCaseTelemetry(pydantic.BaseModel):
    """Per-case operational telemetry record with stage latency breakdown."""

    case_id: str = pydantic.Field(default_factory=lambda: uuid.uuid4().hex)
    use_case_name: str
    session_id: str = ""
    user_id: str = ""
    trace_id: str = ""
    span_id: str = ""
    transactions_processed: int = 0
    stages: dict[str, float] = pydantic.Field(default_factory=dict)
    total_latency_ms: float = 0.0
    success: bool = True
    error_message: str | None = None
    business_kpi: str = "agent-execution"
    pl_impact_line: str = "ai-platform-opex"
    metadata: dict[str, Any] = pydantic.Field(default_factory=dict)
    timestamp: str = pydantic.Field(default_factory=utc_now_iso)


class AuditLogEntry(pydantic.BaseModel):
    """Immutable compliance audit entry for an agent or database action."""

    model_config = pydantic.ConfigDict(frozen=True)

    trace_id: str
    session_id: str
    user_id: str
    agent_name: str
    input_query: str
    generated_sql: str | None = None
    execution_status: str
    rows_returned: int | None = None
    error_message: str | None = None
    latency_ms: float = 0.0
    safety_sanitization_status: str = "CLEAN"
    timestamp: str = pydantic.Field(default_factory=utc_now_iso)


class ConversationTurn(pydantic.BaseModel):
    """Single message turn within a conversational session."""

    model_config = pydantic.ConfigDict(frozen=True)

    id: int | None = None
    session_id: str
    role: str
    content: str
    timestamp: str = pydantic.Field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = pydantic.Field(default_factory=dict)


class MemoryItem(pydantic.BaseModel):
    """Persistent long-term user fact or preference entry."""

    model_config = pydantic.ConfigDict(frozen=True)

    memory_id: str = ""
    user_id: str
    fact: str
    memory_type: str = "fact"
    score: float | None = None
    created_at: str = pydantic.Field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = pydantic.Field(default_factory=dict)


@dataclasses.dataclass(frozen=True, slots=True)
class SafetyScanResult:
    """Raw outcome from a safety scanner adapter."""

    is_safe: bool
    sanitized_text: str
    violation_category: str | None = None
    confidence: float = 0.0
    source: str = "model_armor"


@dataclasses.dataclass(frozen=True, slots=True)
class DecisionResult:
    """Policy decision returned by inbound or outbound safety checks."""

    allowed: bool
    verdict: SecurityVerdict
    sanitized_text: str
    reason: str = ""
    violation_category: str | None = None
    latency_ms: float = 0.0


@dataclasses.dataclass(frozen=True, slots=True)
class SQLValidationResult:
    """Outcome of AST-level SQL governance validation and rewriting."""

    is_safe: bool
    sanitized_sql: str
    violation_reason: str | None = None
    referenced_tables: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class RateLimitRule:
    """Quota and concurrency parameters for a rate-limited resource."""

    rule_id: str
    rate: int = 60
    period_seconds: float = 60.0
    burst: int = 15
    max_concurrency: int = 25
    wait_timeout_seconds: float = 0.0
    algorithm: RateLimitAlgorithm = RateLimitAlgorithm.TOKEN_BUCKET


@dataclasses.dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Result of an admission check against a configured rate-limit rule."""

    allowed: bool
    key: str
    remaining: float
    retry_after_seconds: float = 0.0
