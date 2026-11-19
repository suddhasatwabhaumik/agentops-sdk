# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Lazy, fail-closed runtime configuration and unified GCP ADC resolution."""

from __future__ import annotations

import functools
import os
import re
from typing import TypeVar

import google.auth
from google.auth import exceptions as auth_exceptions
import pydantic
import pydantic_settings

from agentops_sdk.config import _schemas
from agentops_sdk.domain import exceptions

_T = TypeVar("_T", bound=pydantic_settings.BaseSettings)
_GCP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{1,127}$", re.IGNORECASE)

GCPSettings = _schemas.GCPSettings
TelemetrySettings = _schemas.TelemetrySettings
SecuritySettings = _schemas.SecuritySettings
DatabaseSettings = _schemas.DatabaseSettings
RateLimitSettings = _schemas.RateLimitSettings
PromptSettings = _schemas.PromptSettings


def _first_non_blank(*candidates: str | None) -> str | None:
    for cand in candidates:
        if isinstance(cand, str) and cand.strip():
            return cand.strip()
    return None


def validate_gcp_identifier(value: str, field_name: str = "identifier") -> str:
    """Validates a GCP project, region, dataset, table, or template ID."""
    cleaned = value.strip()
    if (
        not cleaned
        or "/" in cleaned
        or ".." in cleaned
        or not _GCP_ID_RE.match(cleaned)
    ):
        raise ValueError(f"Invalid GCP {field_name}: {value!r}")
    return cleaned


def _validate_cfg_id(value: str, field_name: str) -> str:
    try:
        return validate_gcp_identifier(value, field_name)
    except ValueError as exc:
        raise exceptions.ConfigurationError(str(exc)) from exc


def _load_settings(cls: type[_T]) -> _T:
    try:
        return cls()
    except pydantic.ValidationError as exc:
        raise exceptions.ConfigurationError(
            f"Invalid configuration for {cls.__name__}: {exc}"
        ) from exc


@functools.lru_cache(maxsize=1)
def get_gcp_settings() -> GCPSettings:
    """Returns cached GCPSettings."""
    return _load_settings(GCPSettings)


@functools.lru_cache(maxsize=1)
def get_telemetry_settings() -> TelemetrySettings:
    """Returns cached TelemetrySettings."""
    return _load_settings(TelemetrySettings)


@functools.lru_cache(maxsize=1)
def get_security_settings() -> SecuritySettings:
    """Returns cached SecuritySettings."""
    return _load_settings(SecuritySettings)


@functools.lru_cache(maxsize=1)
def get_database_settings() -> DatabaseSettings:
    """Returns cached DatabaseSettings."""
    return _load_settings(DatabaseSettings)


@functools.lru_cache(maxsize=1)
def get_rate_limit_settings() -> RateLimitSettings:
    """Returns cached RateLimitSettings."""
    return _load_settings(RateLimitSettings)


@functools.lru_cache(maxsize=1)
def get_prompt_settings() -> PromptSettings:
    """Returns cached PromptSettings."""
    return _load_settings(PromptSettings)


def reset_settings_cache() -> None:
    """Clears cached settings instances after environment updates."""
    get_gcp_settings.cache_clear()
    get_telemetry_settings.cache_clear()
    get_security_settings.cache_clear()
    get_database_settings.cache_clear()
    get_rate_limit_settings.cache_clear()
    get_prompt_settings.cache_clear()


def resolve_gcp_project_and_location(
    project_id: str | None = None,
    location: str | None = None,
    *,
    require_project: bool = False,
) -> tuple[str | None, str]:
    """Resolves GCP project ID and region via explicit args, env, then ADC."""
    resolved_loc = (
        _first_non_blank(
            location,
            os.getenv("GOOGLE_CLOUD_LOCATION"),
            os.getenv("GOOGLE_CLOUD_REGION"),
            os.getenv("GCP_REGION"),
            os.getenv("AGENTOPS_LOCATION"),
        )
        or "europe-west4"
    )
    resolved_proj = _first_non_blank(
        project_id,
        os.getenv("GOOGLE_CLOUD_PROJECT"),
        os.getenv("GCP_PROJECT"),
        os.getenv("AGENTOPS_PROJECT_ID"),
    )
    if not resolved_proj:
        try:
            _, adc_project = google.auth.default()
            if isinstance(adc_project, str) and adc_project.strip():
                resolved_proj = adc_project.strip()
        except auth_exceptions.DefaultCredentialsError:
            resolved_proj = None
    if resolved_proj:
        resolved_proj = _validate_cfg_id(resolved_proj, "project_id")
    elif require_project:
        raise exceptions.ConfigurationError(
            "GCP project_id is required; pass project_id or set "
            "GOOGLE_CLOUD_PROJECT."
        )
    return resolved_proj, _validate_cfg_id(resolved_loc, "location")
