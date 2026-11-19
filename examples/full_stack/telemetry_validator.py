# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Validates full-stack SDK telemetry in Prometheus, Tempo, Loki, and Grafana."""

from __future__ import annotations

import time
from typing import Any

from _validator_backends import build_checks
from _validator_checks import FAIL
from _validator_checks import PASS
from _validator_checks import WARN
from _validator_checks import CheckResult
from _validator_checks import ProbeCheck
from _validator_checks import RunLedger
import requests

__all__ = [
    "FAIL",
    "PASS",
    "WARN",
    "CheckResult",
    "RunLedger",
    "TelemetryValidator",
    "print_report",
]


def _any_value(value: dict[str, Any]) -> Any:
    for kind in ("stringValue", "boolValue", "intValue", "doubleValue"):
        if kind in value:
            return value[kind]
    return value


class TelemetryValidator:
    """Queries local Prometheus, Tempo, Loki, and Grafana for run artifacts."""

    def __init__(
        self,
        prometheus_url: str,
        tempo_url: str,
        loki_url: str,
        grafana_url: str | None = None,
        timeout_seconds: float = 90,
        poll_seconds: float = 3,
    ) -> None:
        self.prometheus_url = prometheus_url.rstrip("/")
        self.tempo_url = tempo_url.rstrip("/")
        self.loki_url = loki_url.rstrip("/")
        self.grafana_url = grafana_url.rstrip("/") if grafana_url else None
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self.http = requests.Session()
        self._trace_cache: dict[str, list[dict[str, Any]]] = {}

    def _get(self, url: str, **params: Any) -> Any:
        response = self.http.get(url, params=params, timeout=15)
        response.raise_for_status()
        return response.json()

    def prom(self, query: str) -> list[dict[str, Any]]:
        """Execute an instant PromQL query."""
        return self._get(f"{self.prometheus_url}/api/v1/query", query=query)[
            "data"
        ]["result"]

    def prom_value(self, query: str) -> float | None:
        """Return the scalar value of a single-series PromQL query."""
        result = self.prom(query)
        return float(result[0]["value"][1]) if result else None

    def loki_value(self, query: str) -> float | None:
        """Return the scalar value of an instant LogQL metric query."""
        result = self._get(f"{self.loki_url}/loki/api/v1/query", query=query)[
            "data"
        ]["result"]
        return float(result[0]["value"][1]) if result else None

    def loki_records(
        self, query: str, start: float, end: float
    ) -> list[dict[str, str]]:
        """Fetch Loki log records with labels and metadata merged."""
        data = self._get(
            f"{self.loki_url}/loki/api/v1/query_range",
            query=query,
            start=int(start * 1e9),
            end=int(end * 1e9),
            limit=5000,
            direction="forward",
        )["data"]["result"]
        records: list[dict[str, str]] = []
        for stream in data:
            for value in stream["values"]:
                rec = dict(stream["stream"])
                if len(value) > 2 and isinstance(value[2], dict):
                    for grp in value[2].values():
                        rec.update(grp)
                rec["_line"] = value[1]
                records.append(rec)
        return records

    def tempo_spans(self, trace_id: str) -> list[dict[str, Any]]:
        """Return flattened spans for one trace ID, cached once populated."""
        if trace_id in self._trace_cache:
            return self._trace_cache[trace_id]
        response = self.http.get(
            f"{self.tempo_url}/api/v2/traces/{trace_id}", timeout=15
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        body = response.json()
        spans = self._extract_spans(body.get("trace", body))
        if spans:
            self._trace_cache[trace_id] = spans
        return spans

    def _extract_spans(
        self, trace_body: dict[str, Any]
    ) -> list[dict[str, Any]]:
        spans: list[dict[str, Any]] = []
        batches = trace_body.get("resourceSpans", trace_body.get("batches", []))
        for res_spans in batches:
            for scope_spans in res_spans.get("scopeSpans", []):
                for span in scope_spans.get("spans", []):
                    span["attrs"] = {
                        a["key"]: _any_value(a["value"])
                        for a in span.get("attributes", [])
                    }
                    spans.append(span)
        return spans

    def validate(self, ledger: RunLedger) -> list[CheckResult]:
        """Poll probes until every check passes or timeout expires."""
        checks = build_checks(self, ledger)
        last: dict[int, str] = {}
        passed: set[int] = set()
        deadline = time.monotonic() + self.timeout_seconds
        print(
            f"  validating: waiting up to {self.timeout_seconds:.0f}s ...",
            flush=True,
        )
        while True:
            self._poll_once(checks, passed, last)
            if len(passed) == len(checks) or time.monotonic() >= deadline:
                break
            time.sleep(self.poll_seconds)
        return [
            CheckResult(
                c.backend,
                c.name,
                PASS if idx in passed else c.severity,
                last.get(idx, ""),
            )
            for idx, c in enumerate(checks)
        ]

    def _poll_once(
        self, checks: list[ProbeCheck], passed: set[int], last: dict[int, str]
    ) -> None:
        for idx, check in enumerate(checks):
            if idx in passed:
                continue
            try:
                ok, detail = check.probe()
            except requests.RequestException as err:
                url = (
                    err.request.url.split("?")[0]
                    if err.request is not None
                    else ""
                )
                ok, detail = False, f"{type(err).__name__} calling {url}"
            last[idx] = detail
            if ok:
                passed.add(idx)


def print_report(
    ledger: RunLedger,
    results: list[CheckResult],
    grafana_url: str | None = None,
) -> int:
    """Print per-backend validation report and return CLI exit code."""
    print(f"\nValidation - run {ledger.run_id} via {ledger.target}")
    for res in results:
        print(
            f"  {res.status:<4}  {res.backend:<14} {res.name:<42} {res.detail}"
        )
    failed = [r for r in results if r.status == FAIL]
    warned = [r for r in results if r.status == WARN]
    passed_count = len(results) - len(failed) - len(warned)
    print(
        f"\n  {passed_count} passed, {len(failed)} failed, "
        f"{len(warned)} warnings"
    )
    if grafana_url:
        print(
            f"  dashboard: {grafana_url}/d/agentops-sdk-overview"
            f"?var-instance={ledger.run_id}&var-ingest_path={ledger.target}"
        )
    if ledger.trace_ids:
        print(
            f"  a trace  : {grafana_url or ''}/explore  ->  Tempo  ->  "
            f"{ledger.trace_ids[0]}"
        )
    return 1 if failed else 0
