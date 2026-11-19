# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Shared validation dataclasses, constants, and Prometheus checks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
import time
from typing import Any

PASS = "PASS"  # noqa: S105
FAIL = "FAIL"
WARN = "WARN"

EXPECTED_SPAN_NAMES = (
    "agent_flow:{flow}",
    "agent_run:inventory_agent",
    "agent_run:answer_agent",
    "agent_run:price_sync_agent",
    "check_content_safety",
)

EXPECTED_SPAN_ATTRIBUTES = {
    "business.kpi_id": "trace_agent_flow",
    "agentops.agent.urn": "trace_agent_span",
    "safety.is_safe": "SecurityGuard",
    "sql.ast.is_safe": "validate_sql_query_decorator",
    "tool.validation.status": "validate_tool_output",
    "tool.validation.error": "validate_tool_output (retries exhausted)",
    "llm.usage.prompt_tokens": "log_llm_metrics",
    "agentops.privacy.pii_scrubbed": "OutputDLPSanitizer",
    "ingest_path": "collector pipeline",
}

SDK_METRICS = {
    "agentops.rate_limit.requests.total": "agentops_rate_limit_requests_total",
    "agentops.rate_limit.rejected.total": "agentops_rate_limit_rejected_total",
    "agentops.rate_limit.concurrency.active": (
        "agentops_rate_limit_concurrency_active"
    ),
    "agentops.prompt.loads.total": "agentops_prompt_loads_total",
    "agentops.prompt.renders.total": "agentops_prompt_renders_total",
    "agentops.prompt.fallbacks.total": "agentops_prompt_fallbacks_total",
}

PIPELINE_JOBS = {"alloy": "alloy", "otel-collector": "otel-collector"}


@dataclass
class RunLedger:
    """Counters recorded by the workload to compare with stored backends."""

    run_id: str
    target: str
    service_name: str
    flow_name: str
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    iterations: int = 0
    trace_ids: list[str] = field(default_factory=list)
    flows: dict[str, int] = field(default_factory=dict)
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    rate_limit_checks: int = 0
    rate_limit_rejections: int = 0
    audit_entries: int = 0
    case_records: int = 0


@dataclass
class CheckResult:
    """Outcome of a single telemetry validation probe."""

    backend: str
    name: str
    status: str
    detail: str


@dataclass
class ProbeCheck:
    """Executable validation probe with target backend and severity."""

    backend: str
    name: str
    probe: Callable[[], tuple[bool, str]]
    severity: str = FAIL


def fmt_num(value: float | None) -> str:
    """Format an optional metric scalar for report output."""
    return "no data" if value is None else f"{value:g}"


def _exact_counter(
    client: Any, scope: str, instrument: str, expected: int
) -> Callable[[], tuple[bool, str]]:
    def probe() -> tuple[bool, str]:
        name = SDK_METRICS[instrument]
        value = client.prom_value(f"sum({name}{{{scope}}})")
        return (
            value == expected,
            f"{name} = {fmt_num(value)} (workload counted {expected})",
        )

    return probe


def _positive_counter(
    client: Any, scope: str, instrument: str, extra: str = ""
) -> Callable[[], tuple[bool, str]]:
    def probe() -> tuple[bool, str]:
        name = SDK_METRICS[instrument]
        value = client.prom_value(f"sum({name}{{{scope}{extra}}})")
        return bool(value), f"{name}{{{extra.lstrip(', ')}}} = {fmt_num(value)}"

    return probe


def _probe_allowed_and_rejected(client: Any, scope: str) -> tuple[bool, str]:
    name = SDK_METRICS["agentops.rate_limit.requests.total"]
    series = client.prom(f"sum by (allowed) ({name}{{{scope}}})")
    seen = {s["metric"].get("allowed"): s["value"][1] for s in series}
    return {"True", "False"} <= seen.keys(), f"by allowed: {seen or 'none'}"


def _probe_concurrency_released(client: Any, scope: str) -> tuple[bool, str]:
    name = SDK_METRICS["agentops.rate_limit.concurrency.active"]
    value = client.prom_value(f"sum({name}{{{scope}}})")
    return value == 0, f"{name} = {fmt_num(value)} (released -> 0)"


_POS_PROMPT_SPECS = (
    (
        "prompt loads counter",
        "agentops.prompt.loads.total",
        ', backend="local", status="success"',
    ),
    (
        "prompt loads via Local fallback",
        "agentops.prompt.loads.total",
        ', backend="local_fallback"',
    ),
    (
        "prompt renders counter",
        "agentops.prompt.renders.total",
        ', engine="jinja2_sandboxed"',
    ),
    ("prompt fallbacks counter", "agentops.prompt.fallbacks.total", ""),
)


def prometheus_checks(client: Any, ledger: RunLedger) -> list[ProbeCheck]:
    """Build Prometheus SDK counter and gauge validation probes."""
    scope = f'instance="{ledger.run_id}", ingest_path="{ledger.target}"'
    rl_req = "agentops.rate_limit.requests.total"
    rl_rej = "agentops.rate_limit.rejected.total"
    checks = [
        ProbeCheck(
            "prometheus",
            "rate-limit requests counter",
            _exact_counter(client, scope, rl_req, ledger.rate_limit_checks),
        ),
        ProbeCheck(
            "prometheus",
            "rate-limit rejected counter",
            _exact_counter(client, scope, rl_rej, ledger.rate_limit_rejections),
        ),
        ProbeCheck(
            "prometheus",
            "rate-limit requests allowed and rejected",
            lambda: _probe_allowed_and_rejected(client, scope),
        ),
        ProbeCheck(
            "prometheus",
            "rate-limit concurrency gauge",
            lambda: _probe_concurrency_released(client, scope),
        ),
    ]
    checks.extend(
        ProbeCheck("prometheus", n, _positive_counter(client, scope, inst, ext))
        for n, inst, ext in _POS_PROMPT_SPECS
    )
    return checks
