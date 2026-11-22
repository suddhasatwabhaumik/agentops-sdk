# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Drives every telemetry surface against the local observability stack."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse
import uuid

from _validator_checks import RunLedger
from _workload import ENVIRONMENT
from _workload import FLOW_NAME
from _workload import SERVICE_NAME
from _workload import build_context
from _workload import run_workload
from telemetry_validator import TelemetryValidator
from telemetry_validator import print_report

from agentops_sdk import configure_telemetry
from agentops_sdk import reset_telemetry
from agentops_sdk.telemetry import TelemetryHandles

HERE = Path(__file__).resolve().parent
SERVICE_NAMESPACE = "agentops"

TARGETS = {
    "alloy": "http://localhost:4317",
    "otel-collector": "http://localhost:14317",
}


def bootstrap_telemetry(
    endpoint: str, run_id: str, verbose: bool, *, hermetic: bool = False
) -> TelemetryHandles:
    """Configure OTLP or off-mode traces, metrics, and stdlib log forwarding."""
    os.environ["OTEL_RESOURCE_ATTRIBUTES"] = ",".join(
        [
            f"service.namespace={SERVICE_NAMESPACE}",
            f"service.instance.id={run_id}",
            f"deployment.environment.name={ENVIRONMENT}",
        ]
    )
    os.environ["OTEL_METRIC_EXPORT_INTERVAL"] = "5000"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if verbose:
        console = logging.StreamHandler()
        console.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
            )
        )
        root.addHandler(console)
    else:
        root.addHandler(logging.NullHandler())

    if hermetic:
        return configure_telemetry(mode="off", service_name=SERVICE_NAME)
    return configure_telemetry(
        mode="otlp",
        service_name=SERVICE_NAME,
        collector_url=endpoint,
    )


def assert_endpoint_reachable(endpoint: str) -> None:
    """Verify the target OTLP collector port is listening before running."""
    url = urlparse(endpoint)
    try:
        with socket.create_connection((url.hostname, url.port), timeout=3):
            return
    except OSError as err:
        raise SystemExit(
            f"Nothing is listening on {endpoint} ({err}).\n"
            f"Start stack: docker compose -f {HERE / 'docker-compose.yaml'} "
            "up -d\nOr run with --hermetic (or MODE=hermetic)."
        ) from err


def _assert_hermetic_ledger(ledger: RunLedger) -> int:
    """Validate local SDK assertions when running without Docker backends."""
    expected_outcomes = {
        "answered",
        "blocked_by_safety",
        "blocked_by_sql_guard",
        "tool_output_rejected",
        "llm_error",
    }
    missing = expected_outcomes - ledger.flows.keys()
    if missing:
        print(f"FAIL: missing scenario outcomes: {sorted(missing)}")
        return 1
    if ledger.rate_limit_rejections <= 0:
        print("FAIL: expected rate_limit_rejections > 0")
        return 1
    if ledger.audit_entries <= 0 or ledger.case_records <= 0:
        print("FAIL: expected audit_entries > 0 and case_records > 0")
        return 1
    if ledger.memory_recalls <= 0 or ledger.vertex_eval_runs <= 0:
        print("FAIL: expected memory_recalls > 0 and vertex_eval_runs > 0")
        return 1
    print(
        f"  [PASS] Hermetic SDK assertions: {sum(ledger.flows.values())} "
        f"flows, {ledger.memory_recalls} memory recalls, "
        f"{ledger.vertex_eval_runs} vertex eval runs, "
        f"{ledger.rate_limit_rejections} RL rejections."
    )
    return 0


def run_single_target(args: argparse.Namespace, target: str) -> int:
    """Execute workload and validation against one OTLP or hermetic target."""
    hermetic = args.hermetic or os.environ.get("MODE", "").lower() == "hermetic"
    endpoint = TARGETS[target]
    if not hermetic:
        assert_endpoint_reachable(endpoint)
    run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    ledger = RunLedger(
        run_id=run_id,
        target="hermetic" if hermetic else target,
        service_name=SERVICE_NAME,
        flow_name=FLOW_NAME,
    )
    mode_label = "hermetic" if hermetic else f"{target}: {endpoint}"
    print(f"AgentOps SDK full-stack run {run_id} ({mode_label})")

    handles = bootstrap_telemetry(
        endpoint, run_id, args.verbose, hermetic=hermetic
    )
    try:
        ctx = build_context(ledger, HERE / "prompts", gcp_mode=args.gcp)
        asyncio.run(run_workload(ctx, args.iterations, args.loop))
        ledger.finished_at = time.time()
        print("  flushing telemetry ...", flush=True)
        handles.shutdown()
    finally:
        reset_telemetry()

    print(
        f"  emitted  : {len(ledger.trace_ids)} traces, {ledger.llm_calls} LLM "
        f"({ledger.input_tokens} in / {ledger.output_tokens} out), "
        f"{ledger.rate_limit_rejections}/{ledger.rate_limit_checks} RL rej, "
        f"{ledger.audit_entries} audits, {ledger.case_records} cases"
    )
    if hermetic:
        return _assert_hermetic_ledger(ledger)
    if args.no_validate:
        return 0
    validator = TelemetryValidator(
        prometheus_url=args.prometheus_url,
        tempo_url=args.tempo_url,
        loki_url=args.loki_url,
        grafana_url=args.grafana_url,
        timeout_seconds=args.timeout,
    )
    results = validator.validate(ledger)
    return print_report(ledger, results, grafana_url=args.grafana_url)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=[*TARGETS, "both"], default="alloy")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--loop", type=float, default=0)
    parser.add_argument("--no-validate", action="store_true")
    parser.add_argument("--hermetic", action="store_true")
    parser.add_argument("--gcp", action="store_true")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument("--tempo-url", default="http://localhost:3200")
    parser.add_argument("--loki-url", default="http://localhost:3100")
    parser.add_argument("--grafana-url", default="http://localhost:3000")
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint supporting alloy, otel-collector, both, or hermetic."""
    args = _parse_args()
    if args.hermetic or args.target != "both":
        target = "alloy" if args.target == "both" else args.target
        return run_single_target(args, target)

    exit_code = 0
    for target in TARGETS:
        argv = [
            a
            for a in sys.argv[1:]
            if a not in ("both", "--target", "--target=both")
        ]
        exit_code |= subprocess.call(
            [sys.executable, __file__, "--target", target, *argv]
        )
        print()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
