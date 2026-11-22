# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""JWT authentication, SQL AST guard, and tenant scoping for MCP tools."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
import contextlib
import contextvars
import os
import sys
import threading
from typing import Any

from agentops_sdk.domain import context as sdk_context
from agentops_sdk.domain import exceptions
from agentops_sdk.security import auth
from agentops_sdk.sql import ast_guard
from agentops_sdk.sql import rls

_ADMIN_ROLES = frozenset({"admin", "superadmin"})
_HEADERS_CTX: contextvars.ContextVar[Mapping[str, str] | None] = (
    contextvars.ContextVar("mcp_request_headers", default=None)
)
_JWT_CONFIG_KEYS = (
    "AGENTOPS_IDENTITY__JWKS_URL",
    "ENTRA_JWKS_URL",
    "JWT_SECRET_KEY",
    "JWT_PUBLIC_KEY",
    "MCP_JWT_PUBLIC_KEY",
    "AGENTOPS_IDENTITY__CLIENT_ID",
    "ENTRA_CLIENT_ID",
    "AGENTOPS_IDENTITY__ISSUER",
    "ENTRA_ISSUER",
    "AGENTOPS_IDENTITY__TENANT_ID",
    "ENTRA_TENANT_ID",
    "AGENTOPS_IDENTITY__ALLOWED_JWKS_HOSTS",
)
_MAX_ENGINE_CACHE_SIZE = 16
_JWT_ENGINE_LOCK = threading.Lock()
_JWT_ENGINE_CACHE: dict[tuple[str, ...], auth.JWTAuthEngine] = {}


def _jwt_config_key() -> tuple[str, ...]:
    env_mode = os.getenv("ENVIRONMENT", "").strip().lower()
    return (*(os.getenv(k, "").strip() for k in _JWT_CONFIG_KEYS), env_mode)


def get_jwt_engine() -> auth.JWTAuthEngine:
    """Returns a cached JWTAuthEngine keyed by the active auth env config."""
    key = _jwt_config_key()
    with _JWT_ENGINE_LOCK:
        engine = _JWT_ENGINE_CACHE.get(key)
        if engine is None:
            if len(_JWT_ENGINE_CACHE) >= _MAX_ENGINE_CACHE_SIZE:
                _JWT_ENGINE_CACHE.clear()
            engine = auth.JWTAuthEngine()
            _JWT_ENGINE_CACHE[key] = engine
        return engine


def clear_jwt_engine_cache() -> None:
    """Clears cached JWTAuthEngine instances."""
    with _JWT_ENGINE_LOCK:
        _JWT_ENGINE_CACHE.clear()


@contextlib.contextmanager
def bind_request_headers(headers: Mapping[str, str] | None) -> Iterator[None]:
    """Binds incoming HTTP request headers for the active call scope."""
    token = _HEADERS_CTX.set(dict(headers) if headers else None)
    try:
        yield
    finally:
        _HEADERS_CTX.reset(token)


def is_auth_required() -> bool:
    """Returns True unless MCP_REQUIRE_AUTH is explicitly disabled."""
    raw = os.getenv("MCP_REQUIRE_AUTH", "true").strip().lower()
    return raw not in {"false", "0", "no"}


def get_allowed_tables() -> list[str] | None:
    """Loads comma-separated table allowlist from MCP_ALLOWED_TABLES."""
    raw = os.getenv("MCP_ALLOWED_TABLES", "").strip()
    return [t.strip() for t in raw.split(",") if t.strip()] if raw else None


def _header_auth(headers: Mapping[str, str] | None) -> str:
    if not headers:
        return ""
    for key, val in headers.items():
        if key.lower() == "authorization" and isinstance(val, str):
            return val.strip()
    return ""


def _fastmcp_headers() -> Mapping[str, str] | None:
    fastmcp = sys.modules.get("mcp.server.fastmcp")
    get_ctx = getattr(fastmcp, "get_context", None)
    if not callable(get_ctx):
        return None
    try:
        ctx = get_ctx()
        req = getattr(getattr(ctx, "request_context", None), "request", None)
        hdrs = getattr(req, "headers", None)
        return hdrs if isinstance(hdrs, Mapping) else None
    except (AttributeError, LookupError, RuntimeError):
        return None


def resolve_bearer_token(
    *,
    token: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> str:
    """Resolves caller bearer token from args, headers, or active scope."""
    candidates = (
        token or "",
        _header_auth(headers),
        _header_auth(_HEADERS_CTX.get()),
        _header_auth(_fastmcp_headers()),
        sdk_context.current_execution_scope().identity_token or "",
    )
    for raw in candidates:
        cleaned = raw.strip()
        if cleaned.lower().startswith("bearer "):
            cleaned = cleaned[7:].strip()
        if cleaned:
            return cleaned
    return ""


def authenticate_caller(
    *,
    token: str | None = None,
    headers: Mapping[str, str] | None = None,
    jwt_engine: auth.JWTAuthEngine | None = None,
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Verifies caller JWT and returns decoded claims and auth headers."""
    resolved = resolve_bearer_token(token=token, headers=headers)
    if is_auth_required() and not resolved:
        raise exceptions.AuthenticationError(
            "Missing bearer token (MCP_REQUIRE_AUTH is enabled)."
        )
    if not resolved:
        return None, {}
    engine = jwt_engine if jwt_engine is not None else get_jwt_engine()
    claims = engine.decode_token(resolved, expected_type="access")
    return claims, {"Authorization": f"Bearer {resolved}"}


def authorize_query(
    query: str,
    *,
    token: str | None = None,
    dialect: str = "bigquery",
    allowed_tables: Sequence[str] | None = None,
    headers: Mapping[str, str] | None = None,
    jwt_engine: auth.JWTAuthEngine | None = None,
) -> tuple[str, dict[str, str]]:
    """Verifies caller JWT first, then checks SQL AST and injects RLS."""
    claims, out_headers = authenticate_caller(
        token=token, headers=headers, jwt_engine=jwt_engine
    )
    tables = get_allowed_tables() if allowed_tables is None else allowed_tables
    validation = ast_guard.validate_sql_query(
        query, dialect=dialect, allowed_tables=tables
    )
    if not validation.is_safe:
        raise exceptions.SQLGovernanceError(
            validation.violation_reason or "SQL AST security violation."
        )
    sql = validation.sanitized_sql
    if claims is not None:
        if "tenant_id" not in claims and "tid" in claims:
            claims = {**claims, "tenant_id": claims["tid"]}
        sql = rls.apply_row_level_policy(sql, claims, dialect=dialect)
    return sql, out_headers


def validate_and_authorize_sql(
    query_template: str,
    *,
    dialect: str = "bigquery",
    token: str | None = None,
    allowed_tables: Sequence[str] | None = None,
    headers: Mapping[str, str] | None = None,
    jwt_engine: auth.JWTAuthEngine | None = None,
) -> tuple[bool, str, dict[str, str]]:
    """Validates SQL, verifies JWT, and returns (ok, sql_or_error, headers)."""
    try:
        sql, out_headers = authorize_query(
            query_template,
            token=token,
            dialect=dialect,
            allowed_tables=allowed_tables,
            headers=headers,
            jwt_engine=jwt_engine,
        )
    except exceptions.ConfigurationError as exc:
        return False, f"Configuration Error: {exc}", {}
    except exceptions.AuthenticationError as exc:
        return False, f"Authentication Failure: {exc}", {}
    except exceptions.SQLGovernanceError as exc:
        return False, f"SQL AST Security Violation: {exc}", {}
    return True, sql, out_headers


def _coerce_claim_strings(raw: Any, claim_name: str) -> set[str]:
    if isinstance(raw, str):
        return {raw}
    seq_types = (Sequence, set, frozenset)
    if isinstance(raw, bytes | bytearray) or not isinstance(raw, seq_types):
        raise exceptions.AuthenticationError(f"Malformed {claim_name} claim.")
    if not all(isinstance(item, str) for item in raw):
        raise exceptions.AuthenticationError(f"Malformed {claim_name} claim.")
    return set(raw)


def _check_entity_scope(
    entity_type: str,
    entity_ids: Sequence[str],
    claims: Mapping[str, Any],
    tenant_id: str,
) -> None:
    if (raw_t := claims.get("allowed_entity_types")) is not None and (
        entity_type not in _coerce_claim_strings(raw_t, "allowed_entity_types")
    ):
        raise exceptions.AuthenticationError(
            f"Entity type {entity_type!r} outside tenant scope."
        )
    if (raw_i := claims.get("allowed_entity_ids")) is not None:
        allowed_ids = _coerce_claim_strings(raw_i, "allowed_entity_ids")
        if any(eid not in allowed_ids for eid in entity_ids):
            raise exceptions.AuthenticationError(
                "entity_ids outside tenant scope."
            )
    if any(":" in e and e.split(":", 1)[0] != tenant_id for e in entity_ids):
        raise exceptions.AuthenticationError(
            "Cross-tenant entity_id forbidden."
        )


def _validate_feature_lookup_inputs(
    entity_type: str, entity_ids: Sequence[str]
) -> str:
    if not isinstance(entity_type, str) or isinstance(entity_ids, str | bytes):
        raise ValueError("entity_type must be str and entity_ids a sequence.")
    clean_type = entity_type.strip()
    if not clean_type or not entity_ids:
        raise ValueError("entity_type and entity_ids must be non-empty.")
    if any(
        not isinstance(e, str) or not e.strip() or any(c.isspace() for c in e)
        for e in entity_ids
    ):
        raise ValueError("entity_ids must be non-empty without whitespace.")
    return clean_type


def _extract_tenant_claim(claims: Mapping[str, Any]) -> str:
    raw = claims.get("tenant_id")
    if raw is None:
        raw = claims.get("tid")
    if raw is None:
        return ""
    if isinstance(raw, bool) or not isinstance(raw, str | int | float):
        raise exceptions.AuthenticationError("Malformed tenant_id claim.")
    return str(raw).strip()


def authorize_feature_store_tenant(
    entity_type: str,
    entity_ids: Sequence[str],
    claims: Mapping[str, Any] | None,
) -> str | None:
    """Enforces tenant_id and entity scope rules for Feature Store queries."""
    clean_type = _validate_feature_lookup_inputs(entity_type, entity_ids)
    raw_env = os.getenv("MCP_ALLOWED_ENTITY_TYPES", "")
    env_types = [t.strip() for t in raw_env.split(",") if t.strip()]
    if env_types and clean_type not in env_types:
        raise exceptions.AuthenticationError(
            f"Entity type {clean_type!r} is not allowlisted."
        )
    if claims is None:
        return None
    raw_r = claims.get("role")
    role = raw_r.strip().lower() if isinstance(raw_r, str) else ""
    tenant_id = _extract_tenant_claim(claims)
    if role in _ADMIN_ROLES:
        return tenant_id or None
    if not tenant_id:
        raise exceptions.AuthenticationError(
            "Missing tenant_id claim for Feature Store lookup."
        )
    _check_entity_scope(clean_type, entity_ids, claims, tenant_id)
    return tenant_id
