# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Ambient user identity propagation and safe secret injection."""

from __future__ import annotations

import collections
from collections.abc import Callable, Iterator
import contextlib
import inspect
import logging
import os
import threading
from typing import Any, ParamSpec, TypeVar

from agentops_sdk import ports
from agentops_sdk.config import settings
from agentops_sdk.domain import context
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import lifecycle
from agentops_sdk.security import auth
from agentops_sdk.security import secrets

logger = logging.getLogger(__name__)

_P = ParamSpec("_P")
_R = TypeVar("_R")

_DEV_ENVIRONMENTS = frozenset({"dev", "development", "local", "test"})
_MAX_DEFAULT_STORES = 64
_STORE_LOCK = threading.Lock()
_DEFAULT_STORES: collections.OrderedDict[str, secrets.SecretManagerStore] = (
    collections.OrderedDict()
)


def reset_default_secret_stores() -> None:
    """Closes and clears pooled default SecretManagerStore instances."""
    with _STORE_LOCK:
        stores = list(_DEFAULT_STORES.values())
        _DEFAULT_STORES.clear()
    for store in stores:
        with contextlib.suppress(Exception):
            store.close()


def _get_default_store(
    project_id: str | None,
) -> secrets.SecretManagerStore:
    """Returns a bounded pooled SecretManagerStore keyed by GCP project_id."""
    proj, _ = settings.resolve_gcp_project_and_location(
        project_id, None, require_project=True
    )
    resolved = proj or ""
    evicted: list[secrets.SecretManagerStore] = []
    with _STORE_LOCK:
        store = _DEFAULT_STORES.get(resolved)
        if store is not None:
            _DEFAULT_STORES.move_to_end(resolved)
            return store
        store = secrets.SecretManagerStore(project_id=resolved)
        _DEFAULT_STORES[resolved] = store
        while len(_DEFAULT_STORES) > _MAX_DEFAULT_STORES:
            _, old = _DEFAULT_STORES.popitem(last=False)
            evicted.append(old)
    for old in evicted:
        with contextlib.suppress(Exception):
            old.close()
    return store


def get_current_identity() -> context.ExecutionScope:
    """Returns the immutable ExecutionScope for the current call context."""
    return context.current_execution_scope()


def get_user_identity_token() -> str | None:
    """Returns the bearer token bound to the current ExecutionScope, if any."""
    return context.current_execution_scope().identity_token


def fetch_secret(
    secret_id: str,
    *,
    version: str = "latest",
    project_id: str | None = None,
    store: ports.security.SecretStorePort | None = None,
) -> str:
    """Fetches a secret imperatively from SecretStorePort."""
    clean_id = secrets.validate_secret_id(secret_id)
    active_store = (
        store if store is not None else _get_default_store(project_id)
    )
    return active_store.get_secret(clean_id, version=version)


def _bind_secret_argument(
    sig: inspect.Signature,
    has_var_kw: bool,
    param_name: str,
    value: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    if param_name in sig.parameters:
        try:
            bound = sig.bind_partial(*args, **kwargs)
            param_kind = sig.parameters[param_name].kind
            if param_kind == inspect.Parameter.POSITIONAL_ONLY:
                bound.apply_defaults()
            bound.arguments[param_name] = value
            return bound.args, bound.kwargs
        except TypeError as exc:
            raise exceptions.SecurityViolationError(
                f"Cannot bind secret parameter {param_name!r}: {exc}"
            ) from exc
    if has_var_kw:
        call_kwargs = dict(kwargs)
        call_kwargs[param_name] = value
        return args, call_kwargs
    raise exceptions.SecurityViolationError(
        f"Callable does not accept secret parameter {param_name!r}."
    )


def inject_secret(
    secret_id: str,
    *,
    param_name: str = "secret_value",
    version: str = "latest",
    project_id: str | None = None,
    store: ports.security.SecretStorePort | None = None,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Injects a static secret_id into kwargs/positional arg at call time."""
    clean_id = secrets.validate_secret_id(secret_id)
    if not param_name or not param_name.isidentifier():
        raise ValueError(f"Invalid injection param_name: {param_name!r}")

    def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
        sig = inspect.signature(func)
        has_var_kw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )

        def _on_enter(
            args: tuple[Any, ...],
            kwargs: dict[str, Any],
        ) -> tuple[None, tuple[Any, ...], dict[str, Any]]:
            value = fetch_secret(
                clean_id,
                version=version,
                project_id=project_id,
                store=store,
            )
            new_args, new_kwargs = _bind_secret_argument(
                sig, has_var_kw, param_name, value, args, kwargs
            )
            return None, new_args, new_kwargs

        return lifecycle.bind_sync_or_async(func, on_enter=_on_enter)

    return decorator


def _extract_bearer_token(
    sig: inspect.Signature,
    token_param: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    """Extracts bearer token from explicit args/kwargs or outer context."""
    call_kwargs = dict(kwargs)
    has_var_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    raw_val: Any = None
    explicitly_passed = False
    if token_param in call_kwargs:
        explicitly_passed = True
        raw_val = (
            call_kwargs[token_param]
            if token_param in sig.parameters or has_var_kw
            else call_kwargs.pop(token_param)
        )
    elif token_param in sig.parameters:
        bound = sig.bind_partial(*args, **call_kwargs)
        if token_param in bound.arguments:
            explicitly_passed = True
            raw_val = bound.arguments[token_param]
    if not explicitly_passed:
        raw_val = context.current_execution_scope().identity_token
    if not isinstance(raw_val, str) or not raw_val.strip():
        return None, call_kwargs
    cleaned = raw_val.strip()
    if cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip()
    return (cleaned or None), call_kwargs


def _resolve_scope_identity(
    token: str | None,
    jwt_engine: auth.JWTAuthEngine | None,
) -> tuple[str, dict[str, Any]]:
    """Verifies JWT claims or returns isolated empty identity / dev RBAC."""
    if jwt_engine is not None:
        if token is None:
            raise exceptions.AuthenticationError(
                "Missing authentication token for protected operation."
            )
        claims = jwt_engine.decode_token(token, expected_type="access")
        user_id = str(claims.get("sub") or claims.get("user_id") or "")
        return user_id, claims
    if token is not None:
        return "", {}
    dev_user = os.getenv("DEV_RBAC_USER", "").strip()
    env = os.getenv("ENVIRONMENT", "").strip().lower()
    if dev_user and env in _DEV_ENVIRONMENTS:
        return dev_user, {"sub": dev_user, "role": "developer"}
    if dev_user:
        logger.warning("Ignoring DEV_RBAC_USER in non-dev environment %r.", env)
    return "", {}


@contextlib.contextmanager
def _clear_identity_token_when_absent() -> Iterator[None]:
    tok = context.identity_token_ctx.set(None)
    try:
        yield
    finally:
        context.identity_token_ctx.reset(tok)


def _bind_identity_stack(
    sig: inspect.Signature,
    token_param: str,
    jwt_engine: auth.JWTAuthEngine | None,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[contextlib.ExitStack, tuple[Any, ...], dict[str, Any]]:
    token, call_kwargs = _extract_bearer_token(sig, token_param, args, kwargs)
    user_id, claims = _resolve_scope_identity(token, jwt_engine)
    stack = contextlib.ExitStack()
    if token is None and context.identity_token_ctx.get() is not None:
        stack.enter_context(_clear_identity_token_when_absent())
    stack.enter_context(
        context.bind_execution_scope(
            user_id=user_id,
            identity_token=token,
            claims=claims,
        )
    )
    return stack, args, call_kwargs


def pass_user_identity(
    *,
    token_param: str = "auth_token",  # noqa: S107
    jwt_engine: auth.JWTAuthEngine | None = None,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Extracts bearer token, optionally verifies JWT, and binds scope."""

    def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
        sig = inspect.signature(func)

        def _on_enter(
            args: tuple[Any, ...],
            kwargs: dict[str, Any],
        ) -> tuple[
            contextlib.ExitStack,
            tuple[Any, ...],
            dict[str, Any],
        ]:
            return _bind_identity_stack(
                sig, token_param, jwt_engine, args, kwargs
            )

        def _on_finally(stack: contextlib.ExitStack) -> None:
            stack.close()

        return lifecycle.bind_sync_or_async(
            func, on_enter=_on_enter, on_finally=_on_finally
        )

    return decorator
