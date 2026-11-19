# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Loki, pipeline self-telemetry, and Grafana backend probe definitions."""

from __future__ import annotations

import time
from typing import Any

from _validator_checks import PIPELINE_JOBS
from _validator_checks import WARN
from _validator_checks import ProbeCheck
from _validator_checks import RunLedger
from _validator_checks import fmt_num


def _loki_window(ledger: RunLedger) -> str:
    return f"{int(time.time() - ledger.started_at) + 120}s"


def _loki_scope(ledger: RunLedger) -> str:
    return (
        f'{{service_name="{ledger.service_name}"}} '
        f'| service_instance_id="{ledger.run_id}" '
        f'| ingest_path="{ledger.target}"'
    )


def _probe_finops_records(
    client: Any, ledger: RunLedger, finops: str
) -> tuple[bool, str]:
    val = client.loki_value(
        f"sum(count_over_time({finops} [{_loki_window(ledger)}]))"
    )
    return (
        val == ledger.llm_calls,
        f"{fmt_num(val)} FinOps records (exp {ledger.llm_calls})",
    )


def _probe_finops_tokens(
    client: Any, ledger: RunLedger, finops: str
) -> tuple[bool, str]:
    win = _loki_window(ledger)
    got_in = client.loki_value(
        f"sum(sum_over_time({finops} | unwrap llm_input_tokens [{win}]))"
    )
    got_out = client.loki_value(
        f"sum(sum_over_time({finops} | unwrap llm_output_tokens [{win}]))"
    )
    ok = got_in == ledger.input_tokens and got_out == ledger.output_tokens
    return (
        ok,
        f"in/out {fmt_num(got_in)}/{fmt_num(got_out)} "
        f"(exp {ledger.input_tokens}/{ledger.output_tokens})",
    )


def _probe_finops_join(
    client: Any, ledger: RunLedger, finops: str
) -> tuple[bool, str]:
    recs = client.loki_records(finops, ledger.started_at - 60, time.time() + 60)
    joined = sum(1 for r in recs if r.get("trace_id") in ledger.trace_ids)
    return bool(recs) and joined == len(recs), f"{joined}/{len(recs)} joined"


def _probe_eval_records(
    client: Any, ledger: RunLedger, logs: str
) -> tuple[bool, str]:
    cnt = client.loki_value(
        f"sum(count_over_time({logs} "
        f'| event_type="agent_quality_evaluation" [{_loki_window(ledger)}]))'
    )
    return cnt == ledger.flows.get("answered", 0), f"{fmt_num(cnt)} eval recs"


def loki_checks(client: Any, ledger: RunLedger) -> list[ProbeCheck]:
    """Build Loki FinOps and evaluation log probes."""
    logs = _loki_scope(ledger)
    finops = f'{logs} | event_type="finops_llm_telemetry"'
    return [
        ProbeCheck(
            "loki",
            "FinOps record per LLM call",
            lambda: _probe_finops_records(client, ledger, finops),
        ),
        ProbeCheck(
            "loki",
            "FinOps token totals",
            lambda: _probe_finops_tokens(client, ledger, finops),
        ),
        ProbeCheck(
            "loki",
            "FinOps records joined to traces",
            lambda: _probe_finops_join(client, ledger, finops),
        ),
        ProbeCheck(
            "loki",
            "evaluation records",
            lambda: _probe_eval_records(client, ledger, logs),
        ),
    ]


def _probe_receivers(client: Any, job: str) -> tuple[bool, str]:
    series = client.prom(
        'sum by (__name__) ({__name__=~"otelcol_receiver_'
        f'(accepted|refused|failed)_.*", job="{job}"}})'
    )
    vals = {
        s["metric"]["__name__"]
        .removeprefix("otelcol_receiver_")
        .removesuffix("_total"): float(s["value"][1])
        for s in series
    }
    acc = {
        k.removeprefix("accepted_"): v
        for k, v in vals.items()
        if k.startswith("accepted_")
    }
    rej = {
        k: v for k, v in vals.items() if not k.startswith("accepted_") and v > 0
    }
    ok = len(acc) == 3 and all(acc.values()) and not rej
    return ok, f"accepted { ({k: int(v) for k, v in acc.items()}) }"


def _probe_no_failures(client: Any, job: str) -> tuple[bool, str]:
    series = client.prom(
        'sum by (__name__) ({__name__=~"otelcol_exporter_send_failed_.*'
        f'|prometheus_remote_storage_samples_failed_total", job="{job}"}})'
    )
    failed = {
        s["metric"]["__name__"]: s["value"][1]
        for s in series
        if float(s["value"][1]) > 0
    }
    return not failed, f"failed: {failed}" if failed else "no failed exports"


def pipeline_checks(client: Any, path: str) -> list[ProbeCheck]:
    """Build collector self-telemetry probes."""
    job = PIPELINE_JOBS[path]
    return [
        ProbeCheck(
            job,
            "receiver accepting all three signals",
            lambda: _probe_receivers(client, job),
            severity=WARN,
        ),
        ProbeCheck(
            job,
            "exporters report no failures",
            lambda: _probe_no_failures(client, job),
            severity=WARN,
        ),
    ]


def _probe_ds_health(client: Any) -> tuple[bool, str]:
    states = {}
    for uid in ("prometheus", "tempo", "loki"):
        resp = client.http.get(
            f"{client.grafana_url}/api/datasources/uid/{uid}/health", timeout=15
        )
        states[uid] = resp.json().get("status", resp.status_code)
    return all(s == "OK" for s in states.values()), f"health: {states}"


def _probe_dashboards(client: Any) -> tuple[bool, str]:
    found = {
        d["uid"]
        for d in client._get(f"{client.grafana_url}/api/search", type="dash-db")
    }
    wanted = {"agentops-sdk-overview", "agentops-telemetry-pipeline"}
    return wanted <= found, "dashboards provisioned"


def grafana_checks(client: Any) -> list[ProbeCheck]:
    """Build Grafana datasource and dashboard provisioning probes."""
    if not client.grafana_url:
        return []
    return [
        ProbeCheck(
            "grafana",
            "datasources healthy",
            lambda: _probe_ds_health(client),
            severity=WARN,
        ),
        ProbeCheck(
            "grafana",
            "dashboards provisioned",
            lambda: _probe_dashboards(client),
            severity=WARN,
        ),
    ]
