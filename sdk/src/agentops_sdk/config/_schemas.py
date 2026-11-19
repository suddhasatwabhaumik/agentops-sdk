# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Pydantic-settings schema definitions for AgentOps SDK."""

from __future__ import annotations

from typing import Literal

import pydantic
import pydantic_settings

from agentops_sdk.domain import models


def strip_blank_to_none(val: object) -> object:
    """Normalizes empty or whitespace-only strings to None."""
    if isinstance(val, str):
        return val.strip() or None
    return val


def strip_loc_or_default(val: object) -> object:
    """Strips region string or falls back to europe-west4 when blank."""
    if isinstance(val, str):
        return val.strip() or "europe-west4"
    return "europe-west4" if val is None else val


class GCPSettings(pydantic_settings.BaseSettings):
    """Canonical Google Cloud project and region settings."""

    model_config = pydantic_settings.SettingsConfigDict(extra="ignore")

    project_id: str | None = pydantic.Field(
        default=None,
        validation_alias=pydantic.AliasChoices(
            "GOOGLE_CLOUD_PROJECT",
            "GCP_PROJECT",
            "AGENTOPS_PROJECT_ID",
        ),
    )
    location: str = pydantic.Field(
        default="europe-west4",
        validation_alias=pydantic.AliasChoices(
            "GOOGLE_CLOUD_LOCATION",
            "GOOGLE_CLOUD_REGION",
            "GCP_REGION",
            "AGENTOPS_LOCATION",
        ),
    )

    _clean_proj = pydantic.field_validator("project_id", mode="before")(
        strip_blank_to_none
    )
    _clean_loc = pydantic.field_validator("location", mode="before")(
        strip_loc_or_default
    )


class TelemetrySettings(pydantic_settings.BaseSettings):
    """OpenTelemetry and structured logging export settings."""

    model_config = pydantic_settings.SettingsConfigDict(
        env_prefix="OTEL_", extra="ignore"
    )

    mode: models.TelemetryMode = pydantic.Field(
        default=models.TelemetryMode.AUTO,
        validation_alias=pydantic.AliasChoices(
            "AGENTOPS_TELEMETRY_MODE", "OTEL_MODE"
        ),
    )
    collector_url: str | None = pydantic.Field(
        default=None,
        validation_alias=pydantic.AliasChoices(
            "OTEL_COLLECTOR_URL", "OTEL_EXPORTER_OTLP_ENDPOINT"
        ),
    )
    service_name: str = pydantic.Field(default="agentops-service")
    finops_log_name: str = pydantic.Field(
        default="agentops-finops-telemetry",
        validation_alias=pydantic.AliasChoices("AGENTOPS_FINOPS_LOG_NAME"),
    )

    _clean_url = pydantic.field_validator("collector_url", mode="before")(
        strip_blank_to_none
    )

    @pydantic.field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, val: object) -> object:
        if isinstance(val, str):
            return val.strip().lower() or models.TelemetryMode.AUTO
        return val


class SecuritySettings(pydantic_settings.BaseSettings):
    """Google Cloud Model Armor, DLP, and JWT authentication settings."""

    model_config = pydantic_settings.SettingsConfigDict(
        env_prefix="SECURITY_", extra="ignore"
    )

    project_id: str | None = pydantic.Field(
        default=None,
        validation_alias=pydantic.AliasChoices(
            "MODEL_ARMOR_PROJECT_ID",
            "SECURITY_PROJECT_ID",
            "GOOGLE_CLOUD_PROJECT",
            "GCP_PROJECT",
            "AGENTOPS_PROJECT_ID",
        ),
    )
    location: str = pydantic.Field(
        default="europe-west4",
        validation_alias=pydantic.AliasChoices(
            "MODEL_ARMOR_LOCATION",
            "MODEL_ARMOR_REGION",
            "SECURITY_LOCATION",
            "SECURITY_REGION",
            "GOOGLE_CLOUD_LOCATION",
            "GOOGLE_CLOUD_REGION",
            "GCP_REGION",
            "AGENTOPS_LOCATION",
        ),
    )
    input_template_id: str = pydantic.Field(
        default="agentops-input-guard",
        validation_alias=pydantic.AliasChoices(
            "MODEL_ARMOR_INPUT_TEMPLATE",
            "MODEL_ARMOR_TEMPLATE_ID",
            "SECURITY_TEMPLATE_ID",
        ),
    )
    output_template_id: str = pydantic.Field(
        default="agentops-output-guard",
        validation_alias=pydantic.AliasChoices(
            "MODEL_ARMOR_OUTPUT_TEMPLATE",
            "SECURITY_OUTPUT_TEMPLATE_ID",
        ),
    )
    fail_closed: bool = pydantic.Field(
        default=True,
        validation_alias=pydantic.AliasChoices(
            "MODEL_ARMOR_FAIL_CLOSED",
            "MODEL_ARMOR_STRICT_CLOUD",
            "SECURITY_FAIL_CLOSED",
        ),
    )
    pii_masking_enabled: bool = True
    jailbreak_threshold: float = pydantic.Field(default=0.80, ge=0.0, le=1.0)

    _clean_proj = pydantic.field_validator("project_id", mode="before")(
        strip_blank_to_none
    )
    _clean_loc = pydantic.field_validator("location", mode="before")(
        strip_loc_or_default
    )


class DatabaseSettings(pydantic_settings.BaseSettings):
    """Database and Cloud SQL connection settings with SecretStr protection."""

    model_config = pydantic_settings.SettingsConfigDict(
        env_prefix="DB_", extra="ignore"
    )

    type: Literal[
        "snowflake",
        "bigquery",
        "postgres",
        "sap_hana",
        "databricks",
        "feature_store",
    ] = "bigquery"
    host: str = "127.0.0.1"
    port: int = pydantic.Field(default=5432, ge=1, le=65535)
    username: str = ""
    password: pydantic.SecretStr = pydantic.SecretStr("")
    database_name: str = "agentops"
    databricks_http_path: str = pydantic.Field(
        default="",
        validation_alias=pydantic.AliasChoices(
            "DB_DATABRICKS_HTTP_PATH", "DATABRICKS_HTTP_PATH"
        ),
    )
    databricks_token: pydantic.SecretStr = pydantic.Field(
        default=pydantic.SecretStr(""),
        validation_alias=pydantic.AliasChoices(
            "DB_DATABRICKS_TOKEN", "DATABRICKS_TOKEN"
        ),
    )


class RateLimitSettings(pydantic_settings.BaseSettings):
    """Default rate-limiting and concurrency parameters."""

    model_config = pydantic_settings.SettingsConfigDict(
        env_prefix="RATE_LIMIT_", extra="ignore"
    )

    enabled: bool = True
    requests_per_minute: int = pydantic.Field(default=120, ge=1)
    burst_size: int = pydantic.Field(default=30, ge=1)
    max_concurrency: int = pydantic.Field(default=25, ge=1)
    wait_timeout_seconds: float = pydantic.Field(default=0.0, ge=0.0)


class PromptSettings(pydantic_settings.BaseSettings):
    """Default PromptRegistry backend and location settings."""

    model_config = pydantic_settings.SettingsConfigDict(
        env_prefix="PROMPT_REGISTRY_", extra="ignore"
    )

    backend: Literal["local", "git", "vertex_ai", "gcs"] = "local"
    local_dir: str = "prompts"
    gcs_bucket: str | None = None
    gcs_prefix: str = "prompts"
    project_id: str | None = pydantic.Field(
        default=None,
        validation_alias=pydantic.AliasChoices(
            "PROMPT_REGISTRY_PROJECT_ID",
            "GOOGLE_CLOUD_PROJECT",
            "GCP_PROJECT",
            "AGENTOPS_PROJECT_ID",
        ),
    )
    location: str | None = pydantic.Field(
        default=None,
        validation_alias=pydantic.AliasChoices(
            "PROMPT_REGISTRY_LOCATION",
            "GOOGLE_CLOUD_LOCATION",
            "GOOGLE_CLOUD_REGION",
            "GCP_REGION",
            "AGENTOPS_LOCATION",
        ),
    )
    fallback_to_local: bool = pydantic.Field(
        default=False,
        validation_alias=pydantic.AliasChoices(
            "PROMPT_REGISTRY_FALLBACK_TO_LOCAL",
            "PROMPT_REGISTRY_FALLBACK_TO_GIT",
        ),
    )

    _clean_fields = pydantic.field_validator(
        "gcs_bucket", "project_id", "location", mode="before"
    )(strip_blank_to_none)
