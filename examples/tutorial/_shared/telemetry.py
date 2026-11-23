# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Connects tutorial scripts to the local OTLP stack via AgentOps telemetry."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import socket
import time
from typing import TYPE_CHECKING
import urllib.error
from urllib.parse import urlencode
from urllib.parse import urlparse
import urllib.request

from opentelemetry import trace

if TYPE_CHECKING:
    from _shared import settings as _settings
    from agentops_sdk.telemetry import TelemetryHandles


@dataclass
class Telemetry:
    """Active telemetry handles and local stack query helpers."""

    settings: _settings.Settings
    handles: TelemetryHandles

    def flush(self) -> None:
        """Flush and shut down OTLP trace, metric, and log exporters."""
        from agentops_sdk import reset_telemetry

        self.handles.shutdown()
        reset_telemetry()

    def trace_landed(self, trace_id: str, timeout_s: float = 30.0) -> bool:
        """Poll local Tempo to confirm a trace ID landed."""
        url = f"{_tempo_url()}/api/traces/{trace_id}"
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Expected http(s):// URL with host, got {url!r}")
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(  # noqa: S310 - http(s) validated
                    url, timeout=5
                ) as resp:
                    if resp.status == 200:
                        return True
            except (urllib.error.URLError, TimeoutError, OSError):
                pass
            time.sleep(2)
        return False


def _configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(
        logging.Formatter("%(levelname)-7s %(name)s: %(message)s")
    )
    console.addFilter(
        lambda r: r.name.startswith(("agentops_sdk", "agentops."))
    )
    root.addHandler(console)


def start(settings: _settings.Settings) -> Telemetry:
    """Initialize OTLP telemetry against OTEL_COLLECTOR_URL."""
    _assert_reachable(settings.collector_url)
    os.environ["OTEL_RESOURCE_ATTRIBUTES"] = ",".join(
        [
            "service.namespace=agentops-tutorial",
            f"service.instance.id={settings.run_id}",
            f"deployment.environment.name={settings.environment}",
        ]
    )
    os.environ.setdefault("OTEL_METRIC_EXPORT_INTERVAL", "5000")
    _configure_logging()

    from agentops_sdk import configure_telemetry

    handles = configure_telemetry(
        mode="otlp",
        service_name=settings.service_name,
        collector_url=settings.collector_url,
    )
    print(
        f"== telemetry: OTLP traces, metrics and logs -> "
        f"{settings.collector_url}"
    )
    return Telemetry(settings=settings, handles=handles)


def current_trace_id() -> str:
    """Return hex trace ID of the active OpenTelemetry span."""
    return format(trace.get_current_span().get_span_context().trace_id, "032x")


def _tempo_url() -> str:
    return os.getenv("TEMPO_URL", "http://localhost:3200")


def _assert_reachable(collector_url: str) -> None:
    parsed = urlparse(collector_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 4317
    try:
        socket.create_connection((host, port), timeout=3).close()
    except OSError as exc:
        raise SystemExit(
            f"OTLP endpoint {host}:{port} is not reachable ({exc}). "
            "Start stack: bash examples/tutorial/observability/stack.sh up"
        ) from exc


def prometheus_query(expr: str) -> list[tuple[dict[str, str], float]]:
    """Run an instant PromQL query against local Prometheus."""
    base = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
    query_url = f"{base}/api/v1/query?{urlencode({'query': expr})}"
    parsed = urlparse(query_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            f"Expected http(s):// URL with host, got {query_url!r}"
        )
    with urllib.request.urlopen(  # noqa: S310 - http(s) scheme validated above
        query_url, timeout=5
    ) as resp:
        data = json.load(resp)
    return [
        (r["metric"], float(r["value"][1]))
        for r in data.get("data", {}).get("result", [])
    ]
