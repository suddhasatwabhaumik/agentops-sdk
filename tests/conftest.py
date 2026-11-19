# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Shared hermetic test fixtures for AgentOps SDK unit and contract tests."""

from __future__ import annotations

from collections.abc import Iterator
import os
import socket
import sys
from typing import Any

import google.auth
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
import pytest

from agentops_sdk.config import settings
from agentops_sdk.evaluation import genai_evals
from agentops_sdk.evaluation import registry
from agentops_sdk.evaluation import reporting
from agentops_sdk.resilience import rate_limiter
from agentops_sdk.security import dlp
from agentops_sdk.security import identity
from agentops_sdk.security import model_armor
from agentops_sdk.telemetry import case_tracker
from agentops_sdk.telemetry import provider
from mcp_servers.src import auth_guard

_PROJECT_ENV_VARS = (
    "GOOGLE_CLOUD_PROJECT",
    "GCP_PROJECT",
    "AGENTOPS_PROJECT_ID",
    "CLOUDSDK_CORE_PROJECT",
)


def _is_vertex_eval_cli() -> bool:
    for idx, arg in enumerate(sys.argv):
        if arg == "--eval-mode=vertex":
            return True
        if (
            arg == "--eval-mode"
            and idx + 1 < len(sys.argv)
            and sys.argv[idx + 1] == "vertex"
        ):
            return True
    return False


def _is_live_network_allowed(request: pytest.FixtureRequest) -> bool:
    return bool(
        request.node.get_closest_marker("vertex_eval")
        or _is_vertex_eval_cli()
        or os.getenv("ALLOW_LIVE_NETWORK_TESTS") == "1"
    )


def _is_allowed_socket(sock: socket.socket, address: Any) -> bool:
    unix_family = getattr(socket, "AF_UNIX", None)
    if unix_family is not None and sock.family == unix_family:
        return True
    if isinstance(address, tuple) and address:
        host = str(address[0]).strip().lower()
        return host in {"127.0.0.1", "::1", "localhost"} or host.startswith(
            "127."
        )
    return False


def _reset_all_sdk_state() -> None:
    settings.reset_settings_cache()
    provider.reset_telemetry()
    case_tracker.set_default_exporter(None)
    identity.reset_default_secret_stores()
    model_armor.reset_model_armor_clients()
    dlp.reset_dlp_clients()
    genai_evals.clear_metric_registry_cache()
    registry.evaluator_registry.reset()
    reporting.session_reporter.clear()
    rate_limiter.global_rate_limiter.reset()
    rate_limiter.global_sync_rate_limiter.reset()
    auth_guard.clear_jwt_engine_cache()


@pytest.fixture(autouse=True)
def reset_sdk_state(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Resets cached SDK state and isolates host GCP ADC around each test."""
    if not _is_live_network_allowed(request):
        for env_key in _PROJECT_ENV_VARS:
            monkeypatch.delenv(env_key, raising=False)
        monkeypatch.setattr(
            google.auth, "default", lambda *_a, **_kw: (None, None)
        )
    _reset_all_sdk_state()
    try:
        yield
    finally:
        _reset_all_sdk_state()


@pytest.fixture
def otel_span_exporter() -> Iterator[InMemorySpanExporter]:
    """Installs an isolated in-memory OpenTelemetry TracerProvider per test."""
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(tracer_provider)
    try:
        yield exporter
    finally:
        exporter.clear()
        tracer_provider.shutdown()
        provider.reset_telemetry()


@pytest.fixture(autouse=True)
def block_external_network_sockets(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blocks live external socket connections in hermetic unit tests."""
    if _is_live_network_allowed(request):
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _guarded_connect(self: socket.socket, address: Any) -> Any:
        if not _is_allowed_socket(self, address):
            target = address[0] if isinstance(address, tuple) else address
            raise OSError(
                f"Hermetic test isolation: external socket to {target!r} "
                "is blocked."
            )
        return real_connect(self, address)

    def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
        if not _is_allowed_socket(self, address):
            target = address[0] if isinstance(address, tuple) else address
            raise OSError(
                f"Hermetic test isolation: external socket to {target!r} "
                "is blocked."
            )
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
