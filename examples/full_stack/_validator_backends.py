# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Tempo span and backend probe assembly for the telemetry validator."""

from __future__ import annotations

import time
from typing import Any

from _validator_checks import EXPECTED_SPAN_ATTRIBUTES
from _validator_checks import EXPECTED_SPAN_NAMES
from _validator_checks import ProbeCheck
from _validator_checks import RunLedger
from _validator_checks import prometheus_checks
from _validator_logs import grafana_checks
from _validator_logs import loki_checks
from _validator_logs import pipeline_checks


def _all_spans(client: Any, ledger: RunLedger) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for tid in ledger.trace_ids:
        for span in client.tempo_spans(tid):
            spans.append(span)
    return spans


def _probe_traces_retrievable(
    client: Any, ledger: RunLedger
) -> tuple[bool, str]:
    found = sum(1 for tid in ledger.trace_ids if client.tempo_spans(tid))
    total = len(ledger.trace_ids)
    return found == total, f"{found}/{total} trace IDs found by ID"


def _probe_span_names(
    client: Any, ledger: RunLedger, expected: list[str]
) -> tuple[bool, str]:
    names = {s["name"] for s in _all_spans(client, ledger)}
    missing = [n for n in expected if n not in names]
    msg = f"missing {missing}" if missing else f"{len(expected)} span names"
    return not missing, msg


def _probe_span_attrs(client: Any, ledger: RunLedger) -> tuple[bool, str]:
    spans = _all_spans(client, ledger)
    present = set().union(*(s["attrs"] for s in spans)) if spans else set()
    missing = [
        f"{k} ({src})"
        for k, src in EXPECTED_SPAN_ATTRIBUTES.items()
        if k not in present
    ]
    msg = (
        f"missing {missing}"
        if missing
        else f"{len(EXPECTED_SPAN_ATTRIBUTES)} SDK attributes present"
    )
    return not missing, msg


def _probe_ingest_path(client: Any, ledger: RunLedger) -> tuple[bool, str]:
    paths = {s["attrs"].get("ingest_path") for s in _all_spans(client, ledger)}
    return paths == {ledger.target}, f"ingest_path: {sorted(paths)}"


def _probe_retry_events(client: Any, ledger: RunLedger) -> tuple[bool, str]:
    retries = 0
    for span in _all_spans(client, ledger):
        for ev in span.get("events", []):
            if ev.get("name") == "tool.validation.retry":
                retries += 1
    return retries > 0, f"{retries} tool.validation.retry span events"


def _probe_error_flows(
    client: Any, ledger: RunLedger, flow_span: str
) -> tuple[bool, str]:
    keys = ("blocked_by_sql_guard", "tool_output_rejected", "llm_error")
    expected = sum(ledger.flows.get(k, 0) for k in keys)
    errors = sum(
        1
        for s in _all_spans(client, ledger)
        if s["name"] == flow_span
        and s.get("status", {}).get("code") == "STATUS_CODE_ERROR"
    )
    return errors == expected, f"{errors} {flow_span} ERROR (exp {expected})"


def _probe_traceql(
    client: Any, ledger: RunLedger, flow_span: str
) -> tuple[bool, str]:
    query = (
        f'{{ resource.service.instance.id = "{ledger.run_id}" '
        f'&& name = "{flow_span}" }}'
    )
    body = client._get(
        f"{client.tempo_url}/api/search",
        q=query,
        start=int(ledger.started_at) - 60,
        end=int(time.time()) + 60,
        limit=10000,
    )
    found = len(body.get("traces", []))
    total = sum(ledger.flows.values())
    return found == total, f"TraceQL -> {found}/{total} traces"


def _probe_span_metrics(
    client: Any, ledger: RunLedger, expected: list[str]
) -> tuple[bool, str]:
    series = client.prom(
        "sum by (span_name) (traces_spanmetrics_calls_total"
        f'{{service="{ledger.service_name}", ingest_path="{ledger.target}"}})'
    )
    names = {s["metric"].get("span_name") for s in series}
    missing = [n for n in expected if n not in names]
    msg = (
        f"missing {missing}"
        if missing
        else f"traces_spanmetrics_calls_total for {len(names)} spans"
    )
    return not missing, msg


def _tempo_basic_checks(
    client: Any, ledger: RunLedger, exp: list[str]
) -> list[ProbeCheck]:
    return [
        ProbeCheck(
            "tempo",
            "every trace retrievable by ID",
            lambda: _probe_traces_retrievable(client, ledger),
        ),
        ProbeCheck(
            "tempo",
            "SDK span names",
            lambda: _probe_span_names(client, ledger, exp),
        ),
        ProbeCheck(
            "tempo",
            "SDK span attributes",
            lambda: _probe_span_attrs(client, ledger),
        ),
        ProbeCheck(
            "tempo",
            "spans stamped by pipeline",
            lambda: _probe_ingest_path(client, ledger),
        ),
    ]


def _tempo_checks(client: Any, ledger: RunLedger) -> list[ProbeCheck]:
    exp = [n.format(flow=ledger.flow_name) for n in EXPECTED_SPAN_NAMES]
    flow = exp[0]
    checks = _tempo_basic_checks(client, ledger, exp)
    checks.extend(
        [
            ProbeCheck(
                "tempo",
                "tool validation retry events",
                lambda: _probe_retry_events(client, ledger),
            ),
            ProbeCheck(
                "tempo",
                "failed flows marked ERROR",
                lambda: _probe_error_flows(client, ledger, flow),
            ),
            ProbeCheck(
                "tempo",
                "TraceQL search finds every flow",
                lambda: _probe_traceql(client, ledger, flow),
            ),
            ProbeCheck(
                "prometheus",
                "Tempo span metrics",
                lambda: _probe_span_metrics(client, ledger, exp),
            ),
        ]
    )
    return checks


def build_checks(client: Any, ledger: RunLedger) -> list[ProbeCheck]:
    """Assemble all backend validation probes for one workload run."""
    checks = prometheus_checks(client, ledger)
    checks.extend(_tempo_checks(client, ledger))
    checks.extend(loki_checks(client, ledger))
    checks.extend(pipeline_checks(client, ledger.target))
    checks.extend(grafana_checks(client))
    return checks
