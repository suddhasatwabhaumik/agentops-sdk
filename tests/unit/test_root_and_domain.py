# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for root PEP 562 facade, config.settings, and domain modules."""

from __future__ import annotations

import asyncio
import importlib
import time
from typing import Any

from google.auth import exceptions as auth_exceptions
import pytest

import agentops_sdk
from agentops_sdk.config import settings
from agentops_sdk.domain import context
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import lifecycle
from agentops_sdk.domain import models
from agentops_sdk.domain import predicates


def test_root_pep562_exports_and_removed_v2_modules() -> None:
    assert agentops_sdk.__version__ == "1.0.0"
    assert dir(agentops_sdk) == agentops_sdk.__all__
    for symbol in agentops_sdk.__all__:
        assert getattr(agentops_sdk, symbol) is not None
    with pytest.raises(AttributeError):
        _ = agentops_sdk.nonexistent_symbol
    for legacy in ("eval", "decorators", "sync", "safety", "sql_guard"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"agentops_sdk.{legacy}")


def test_settings_validation_and_no_default_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DB_USERNAME", raising=False)
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    db_cfg = settings.DatabaseSettings(_env_file=None)
    assert db_cfg.username == ""
    assert db_cfg.password.get_secret_value() == ""
    assert settings.validate_gcp_identifier("proj-1_ok", "proj") == "proj-1_ok"
    for bad in ("", "../etc", "proj/sub"):
        with pytest.raises(ValueError):
            settings.validate_gcp_identifier(bad, "proj")
    monkeypatch.setenv("SECURITY_JAILBREAK_THRESHOLD", "1.5")
    settings.reset_settings_cache()
    with pytest.raises(exceptions.ConfigurationError):
        settings.get_security_settings()


def test_resolve_gcp_project_and_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("GOOGLE_CLOUD_PROJECT", "GCP_PROJECT", "AGENTOPS_PROJECT_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        settings.google.auth, "default", lambda: (object(), "adc-proj")
    )
    proj, loc = settings.resolve_gcp_project_and_location(
        location="us-central1"
    )
    assert (proj, loc) == ("adc-proj", "us-central1")

    def _no_adc() -> tuple[None, None]:
        raise auth_exceptions.DefaultCredentialsError("missing")

    monkeypatch.setattr(settings.google.auth, "default", _no_adc)
    assert settings.resolve_gcp_project_and_location()[0] is None
    with pytest.raises(exceptions.ConfigurationError):
        settings.resolve_gcp_project_and_location(require_project=True)


async def test_execution_scope_nesting_and_async_isolation() -> None:
    with context.bind_execution_scope(
        session_id="s-1", claims={"sub": "user-a"}
    ) as outer:
        assert outer.user_id == "user-a"
        with context.bind_execution_scope(user_id="override") as inner:
            assert inner.user_id == "override"
        assert context.current_execution_scope().user_id == "user-a"

    async def _worker(uid: str) -> str:
        with context.bind_execution_scope(user_id=uid):
            await asyncio.sleep(0.01)
            return context.current_execution_scope().user_id

    assert await asyncio.gather(_worker("u1"), _worker("u2")) == ["u1", "u2"]


def test_bounded_ttl_cache_and_bind_sync_or_async() -> None:
    with pytest.raises(ValueError):
        lifecycle.BoundedTTLCache(maxsize=0, ttl_seconds=1.0)
    cache: lifecycle.BoundedTTLCache[str, int] = lifecycle.BoundedTTLCache(
        maxsize=2, ttl_seconds=0.04, jitter_ratio=0.0
    )
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1
    cache.set("c", 3)
    assert cache.get("b") is None
    time.sleep(0.06)
    assert cache.get("a") is None
    cache.set("d", 4, ttl_override=5.0)
    assert cache.pop("d") == 4
    cache.clear()

    events: list[str] = []
    wrapped = lifecycle.bind_sync_or_async(
        lambda x: x + 1,
        on_enter=lambda a, kw: (events.append("enter"), a, kw),
        on_success=lambda _s, r: events.append("ok") or r * 2,
        on_finally=lambda _s: events.append("fin"),
    )
    assert wrapped(3) == 8
    assert events == ["enter", "ok", "fin"]


def test_domain_models_predicates_and_exceptions() -> None:
    ctx = models.TelemetryContext(
        business_kpi="  Parts Accuracy!! ", pl_impact_line=""
    )
    assert ctx.business_kpi == "parts-accuracy"
    assert ctx.pl_impact_line == "ai-platform-opex"
    for op, th, ok in (
        ("ge", 0.8, True),
        ("gt", 0.8, False),
        ("le", 0.8, True),
        ("lt", 0.9, True),
        ("eq", 0.8, True),
    ):
        op_literal: Any = op
        pred = predicates.MetricThresholdPredicate("score", th, op_literal)
        assert pred.evaluate({"score": 0.8}) is ok
    with pytest.raises(KeyError):
        predicates.MetricThresholdPredicate("missing", 0.5).evaluate({})
    q_err = exceptions.QualityGateError("fail", {"m": (0.1, 0.5)}, "# md")
    assert isinstance(q_err, exceptions.AgentOpsError)
    assert q_err.failed_metrics == {"m": (0.1, 0.5)}
    t_err = exceptions.ToolValidationError(
        "bad", tool_name="fn", attempts=3, last_error=ValueError("x")
    )
    assert t_err.tool_name == "fn" and t_err.attempts == 3
