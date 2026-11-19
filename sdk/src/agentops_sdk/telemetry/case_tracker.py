# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Per-case operational telemetry tracker and pluggable sink exporters."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
import contextlib
import logging
import os
import threading
import time
from typing import Any, ParamSpec, TypeVar
import uuid

from opentelemetry import trace

from agentops_sdk.domain import context as domain_context
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import models
from agentops_sdk.domain import validation
from agentops_sdk.ports import telemetry as telemetry_ports
from agentops_sdk.telemetry import _sink_helpers
from agentops_sdk.telemetry import decorators
from agentops_sdk.telemetry import gcp_exporters
from agentops_sdk.telemetry import provider

logger = logging.getLogger(__name__)
_P = ParamSpec("_P")
_R = TypeVar("_R")
_DEFAULT_LOCK = threading.Lock()
_DEFAULT_EXPORTER: telemetry_ports.CaseTelemetryExporterPort | None = None
CloudLoggingTelemetryExporter = gcp_exporters.CloudLoggingTelemetryExporter
BigQueryTelemetryExporter = gcp_exporters.BigQueryTelemetryExporter
BatchedGCSTelemetryExporter = gcp_exporters.BatchedGCSTelemetryExporter


class InMemoryTelemetryExporter:
    """Thread-safe in-memory exporter for deterministic testing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[models.UseCaseTelemetry] = []

    @property
    def records(self) -> list[models.UseCaseTelemetry]:
        """Thread-safe snapshot of exported UseCaseTelemetry records."""
        with self._lock:
            return list(self._records)

    def export(self, record: models.UseCaseTelemetry) -> bool:
        """Appends record in memory and returns True."""
        with self._lock:
            self._records.append(record)
        return True

    def clear(self) -> None:
        """Clears all buffered in-memory records."""
        with self._lock:
            self._records.clear()


class LocalFileTelemetryExporter:
    """Rotates NDJSON case telemetry locally with realpath and 0o600 guards."""

    def __init__(
        self,
        file_path: str | os.PathLike[str],
        *,
        base_dir: str | os.PathLike[str] | None = None,
        max_bytes: int = 10_485_760,
        backup_count: int = 5,
    ) -> None:
        self._lock = threading.Lock()
        self._path, self._handler, self._log = (
            _sink_helpers.open_local_jsonl_sink(
                file_path, base_dir, max_bytes, backup_count
            )
        )

    def export(self, record: models.UseCaseTelemetry) -> bool:
        """Appends a JSONL telemetry record and enforces 0o600 permissions."""
        row = record.model_dump(mode="json")
        if record.error_message:
            row["error_message"] = validation.scrub_sensitive_text(
                record.error_message
            )
        try:
            _sink_helpers.write_local_jsonl_record(
                self._lock, self._log, self._handler, self._path, row
            )
            return True
        except OSError as exc:
            msg = f"File write failed: {exc}"
            raise exceptions.TelemetryExportError(msg) from exc

    def close(self) -> None:
        """Flushes and closes the underlying rotating file handler."""
        _sink_helpers.close_local_jsonl_sink(
            self._lock, self._log, self._handler
        )

    def __enter__(self) -> LocalFileTelemetryExporter:
        return self

    def __exit__(self, exc_t: Any, exc_v: Any, tb: Any) -> None:
        del exc_t, exc_v, tb
        self.close()


class CompositeTelemetryExporter:
    """Fan-out exporter forwarding each record to multiple sinks."""

    def __init__(
        self, exporters: Sequence[telemetry_ports.CaseTelemetryExporterPort]
    ) -> None:
        self._exporters = tuple(exporters)

    def export(self, record: models.UseCaseTelemetry) -> bool:
        """Exports record to every sink; returns True iff all succeed."""
        all_ok = True
        for exp in self._exporters:
            try:
                all_ok = exp.export(record) and all_ok
            except exceptions.TelemetryExportError as exc:
                logger.warning("Composite exporter sink failed: %s", exc)
                all_ok = False
        return all_ok


def reset_default_exporter() -> None:
    """Flushes, closes, and clears the process-wide default exporter."""
    global _DEFAULT_EXPORTER
    with _DEFAULT_LOCK:
        exp, _DEFAULT_EXPORTER = _DEFAULT_EXPORTER, None
    for method in ("flush", "close"):
        if exp is not None and callable(fn := getattr(exp, method, None)):
            with contextlib.suppress(Exception):
                fn()


def get_default_exporter() -> telemetry_ports.CaseTelemetryExporterPort:
    """Returns the configured default CaseTelemetryExporterPort."""
    with _DEFAULT_LOCK:
        return _DEFAULT_EXPORTER or CloudLoggingTelemetryExporter()


def set_default_exporter(
    exporter: telemetry_ports.CaseTelemetryExporterPort | None,
) -> None:
    """Sets or resets the process-wide default CaseTelemetryExporterPort."""
    global _DEFAULT_EXPORTER
    with _DEFAULT_LOCK:
        _DEFAULT_EXPORTER = exporter
    if exporter is not None:
        provider.register_reset_hook(reset_default_exporter)


class UseCaseTracker:
    """Sync and async context manager capturing per-case stage latencies."""

    def __init__(
        self,
        use_case_name: str,
        *,
        case_id: str | None = None,
        exporter: telemetry_ports.CaseTelemetryExporterPort | None = None,
        business_kpi: str = "agent-execution",
        pl_impact_line: str = "ai-platform-opex",
    ) -> None:
        self._use_case_name = use_case_name
        self._case_id = case_id or uuid.uuid4().hex
        self._exporter = exporter
        slug = models.sanitize_metric_slug
        self._kpi = slug(business_kpi, "agent-execution")
        self._pl = slug(pl_impact_line, "ai-platform-opex")
        self._stages: dict[str, float] = {}
        self._open_stages: dict[str, float] = {}
        self._metadata: dict[str, Any] = {}
        self._transactions, self._started_at = 0, 0.0
        self._stack: contextlib.ExitStack | None = None
        self._span: trace.Span | None = None
        self.record: models.UseCaseTelemetry | None = None

    def start_stage(self, stage_name: str) -> None:
        """Marks the monotonic start timestamp of a named pipeline stage."""
        self._open_stages[stage_name] = time.perf_counter()

    def end_stage(self, stage_name: str) -> float:
        """Completes a named stage and records its latency in milliseconds."""
        started = self._open_stages.pop(stage_name, None)
        if started is None:
            raise ValueError(f"Stage {stage_name!r} was not started.")
        ms = round((time.perf_counter() - started) * 1000.0, 3)
        prev = self._stages.get(stage_name, 0.0)
        self._stages[stage_name] = round(prev + ms, 3)
        return ms

    def add_transactions(self, count: int = 1) -> None:
        """Increments the number of processed business transactions."""
        if count < 0:
            raise ValueError("Transaction count must be non-negative.")
        self._transactions += count

    def set_metadata(
        self,
        key: str | Mapping[str, Any] | None = None,
        value: Any = None,
        **kw: Any,
    ) -> None:
        """Attaches custom metadata entries to the case telemetry record."""
        if isinstance(key, Mapping):
            self._metadata.update(key)
        elif isinstance(key, str):
            self._metadata[key] = value
        self._metadata.update(kw)

    def _enter_scope(self) -> UseCaseTracker:
        self._started_at = time.perf_counter()
        self._stack = contextlib.ExitStack()
        s_name = f"use_case:{self._use_case_name}"
        self._span = self._stack.enter_context(
            trace.get_tracer("agentops_sdk").start_as_current_span(s_name)
        )
        return self

    def _finalize(
        self, exc_val: BaseException | None
    ) -> models.UseCaseTelemetry:
        for st_name in list(self._open_stages):
            self.end_stage(st_name)
        ms = round((time.perf_counter() - self._started_at) * 1000.0, 3)
        sc = domain_context.current_execution_scope()
        s_c = self._span.get_span_context() if self._span else None
        err = decorators.record_scrubbed_exception(self._span, exc_val)
        if self._stack is not None:
            self._stack.close()
        rec = models.UseCaseTelemetry(
            case_id=self._case_id,
            use_case_name=self._use_case_name,
            session_id=sc.session_id,
            user_id=sc.user_id,
            trace_id=f"{s_c.trace_id:032x}" if s_c and s_c.is_valid else "",
            span_id=f"{s_c.span_id:016x}" if s_c and s_c.is_valid else "",
            transactions_processed=self._transactions,
            stages=dict(self._stages),
            total_latency_ms=ms,
            success=exc_val is None,
            error_message=err,
            business_kpi=self._kpi,
            pl_impact_line=self._pl,
            metadata=dict(self._metadata),
        )
        self.record = rec
        return rec

    def __enter__(self) -> UseCaseTracker:
        return self._enter_scope()

    def __exit__(
        self, exc_t: Any, exc_v: BaseException | None, tb: Any
    ) -> None:
        del exc_t, tb  # Unused.
        (self._exporter or get_default_exporter()).export(self._finalize(exc_v))

    async def __aenter__(self) -> UseCaseTracker:
        return self._enter_scope()

    async def __aexit__(
        self, exc_t: Any, exc_v: BaseException | None, tb: Any
    ) -> None:
        del exc_t, tb  # Unused.
        sink = self._exporter or get_default_exporter()
        await asyncio.to_thread(sink.export, self._finalize(exc_v))


def trace_use_case(
    use_case_name: str,
    *,
    exporter: telemetry_ports.CaseTelemetryExporterPort | None = None,
    business_kpi: str = "agent-execution",
    pl_impact_line: str = "ai-platform-opex",
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Decorates a sync or async callable with a UseCaseTracker lifecycle."""

    def _mk() -> UseCaseTracker:
        return UseCaseTracker(
            use_case_name,
            exporter=exporter,
            business_kpi=business_kpi,
            pl_impact_line=pl_impact_line,
        )

    return lambda fn: decorators.wrap_context_manager(fn, _mk)
