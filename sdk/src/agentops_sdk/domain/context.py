# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Thread-safe and coroutine-safe ambient context propagation via ContextVar."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
import contextlib
import contextvars
import dataclasses
from typing import Any

from agentops_sdk.domain import models

session_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agentops_session_id", default=""
)
user_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agentops_user_id", default=""
)
identity_token_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "agentops_identity_token", default=None
)
identity_claims_ctx: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("agentops_identity_claims", default=None)
)
telemetry_context_ctx: contextvars.ContextVar[
    models.TelemetryContext | None
] = contextvars.ContextVar("agentops_telemetry_context", default=None)


@dataclasses.dataclass(frozen=True, slots=True)
class ExecutionScope:
    """Immutable snapshot of active correlation and identity context."""

    session_id: str
    user_id: str
    identity_token: str | None
    claims: Mapping[str, Any] | None
    telemetry_context: models.TelemetryContext | None


@contextlib.contextmanager
def bind_execution_scope(
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    identity_token: str | None = None,
    claims: Mapping[str, Any] | None = None,
    telemetry_context: models.TelemetryContext | None = None,
) -> Iterator[ExecutionScope]:
    """Binds ambient execution metadata for the duration of a call scope."""
    tokens: list[
        tuple[contextvars.ContextVar[Any], contextvars.Token[Any]]
    ] = []
    if session_id is not None:
        tokens.append((session_id_ctx, session_id_ctx.set(session_id)))
    if user_id is not None:
        tokens.append((user_id_ctx, user_id_ctx.set(user_id)))
    if identity_token is not None:
        tokens.append(
            (identity_token_ctx, identity_token_ctx.set(identity_token))
        )
    if claims is not None:
        tokens.append(
            (identity_claims_ctx, identity_claims_ctx.set(dict(claims)))
        )
    if telemetry_context is not None:
        tokens.append(
            (
                telemetry_context_ctx,
                telemetry_context_ctx.set(telemetry_context),
            )
        )
    try:
        yield current_execution_scope()
    finally:
        for var, tok in reversed(tokens):
            var.reset(tok)


def current_execution_scope() -> ExecutionScope:
    """Returns the currently active ExecutionScope."""
    t_ctx = telemetry_context_ctx.get()
    claims = identity_claims_ctx.get()
    user_id = user_id_ctx.get() or (t_ctx.user_id if t_ctx else "") or ""
    if not user_id and claims:
        user_id = str(claims.get("sub") or claims.get("user_id") or "")
    session_id = (
        session_id_ctx.get() or (t_ctx.session_id if t_ctx else "") or ""
    )
    return ExecutionScope(
        session_id=session_id,
        user_id=user_id,
        identity_token=identity_token_ctx.get(),
        claims=claims,
        telemetry_context=t_ctx,
    )
