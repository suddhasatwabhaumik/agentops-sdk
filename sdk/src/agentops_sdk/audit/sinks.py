# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Concrete and in-memory compliance audit sink adapters."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from google.cloud import bigquery

from agentops_sdk.domain import exceptions
from agentops_sdk.domain import models
from agentops_sdk.telemetry import _sink_helpers

logger = logging.getLogger(__name__)

_AUDIT_BQ_SCHEMA: tuple[bigquery.SchemaField, ...] = (
    bigquery.SchemaField("trace_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("session_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("user_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("agent_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("input_query", "STRING"),
    bigquery.SchemaField("generated_sql", "STRING"),
    bigquery.SchemaField("execution_status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("rows_returned", "INT64"),
    bigquery.SchemaField("error_message", "STRING"),
    bigquery.SchemaField("latency_ms", "FLOAT64"),
    bigquery.SchemaField("safety_sanitization_status", "STRING"),
    bigquery.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED"),
)


class InMemoryAuditAdapter:
    """Thread-safe in-memory AuditSinkPort implementation for testing."""

    def __init__(self) -> None:
        """Initializes an empty thread-safe InMemoryAuditAdapter."""
        self._lock = threading.Lock()
        self._entries: list[models.AuditLogEntry] = []

    @property
    def entries(self) -> list[models.AuditLogEntry]:
        """Thread-safe snapshot of recorded AuditLogEntry items."""
        with self._lock:
            return list(self._entries)

    def write_entry(self, entry: models.AuditLogEntry) -> bool:
        """Appends an immutable AuditLogEntry in memory."""
        with self._lock:
            self._entries.append(entry)
        return True

    def clear(self) -> None:
        """Clears all recorded audit entries."""
        with self._lock:
            self._entries.clear()


def _audit_severity(entry: models.AuditLogEntry) -> str:
    if entry.error_message:
        return "WARNING"
    return (
        "INFO"
        if entry.execution_status.upper() in ("OK", "SUCCESS", "ALLOW")
        else "WARNING"
    )


class CloudLoggingAuditAdapter:
    """Default AuditSinkPort writing structured records to Cloud Logging."""

    def __init__(
        self,
        project_id: str | None = None,
        log_name: str = "agentops-audit-ledger",
        client: Any = None,
    ) -> None:
        """Initializes CloudLoggingAuditAdapter for a project and log name."""
        self._project_id = project_id
        self._log_name = log_name
        self._client = client

    def write_entry(self, entry: models.AuditLogEntry) -> bool:
        """Writes an AuditLogEntry to Cloud Logging or OTLP stdlib logs."""
        payload = {
            **entry.model_dump(mode="json"),
            "event_type": "audit_log_entry",
        }
        try:
            _sink_helpers.write_cloud_logging_record(
                payload,
                project_id=self._project_id,
                log_name=self._log_name,
                client=self._client,
                severity=_audit_severity(entry),
                stdlib_logger=logger,
                log_message=(
                    f"AuditLogEntry agent={entry.agent_name} "
                    f"status={entry.execution_status}"
                ),
                is_compliance_audit=True,
            )
            return True
        except Exception as exc:
            raise exceptions.AuditLedgerError(
                f"Cloud Logging audit write failed: {exc}"
            ) from exc


class BigQueryAuditAdapter:
    """AuditSinkPort writing immutable compliance records to BigQuery."""

    def __init__(
        self,
        project_id: str | None = None,
        dataset_id: str = "agentops_governance",
        table_id: str = "audit_ledger",
        client: Any = None,
        auto_create_table: bool = True,
    ) -> None:
        """Initializes BigQueryAuditAdapter for the target audit table."""
        self._writer = _sink_helpers.PartitionedBQWriter(
            project_id, dataset_id, table_id, client, auto_create_table
        )

    def write_entry(self, entry: models.AuditLogEntry) -> bool:
        """Inserts an AuditLogEntry into BigQuery with bounded Retry."""
        try:
            errors = self._writer.insert_row(
                entry.model_dump(mode="json"), _AUDIT_BQ_SCHEMA
            )
            if errors:
                raise exceptions.AuditLedgerError(
                    f"BigQuery audit errors: {errors}"
                )
            return True
        except exceptions.AuditLedgerError:
            raise
        except Exception as exc:
            raise exceptions.AuditLedgerError(
                f"BQ audit write failed: {exc}"
            ) from exc

    def close(self) -> None:
        """Closes internally managed BigQuery client."""
        self._writer.close()

    def __enter__(self) -> BigQueryAuditAdapter:
        return self

    def __exit__(self, exc_t: Any, exc_v: Any, tb: Any) -> None:
        del exc_t, exc_v, tb
        self.close()


class LocalFileAuditAdapter:
    """Opt-in local JSONL AuditSinkPort with realpath and 0o600 enforcement."""

    def __init__(
        self,
        file_path: str | os.PathLike[str],
        *,
        base_dir: str | os.PathLike[str] | None = None,
        max_bytes: int = 10_485_760,
        backup_count: int = 5,
    ) -> None:
        """Initializes LocalFileAuditAdapter with path and rotation limits."""
        self._lock = threading.Lock()
        self._path, self._handler, self._logger = (
            _sink_helpers.open_local_jsonl_sink(
                file_path, base_dir, max_bytes, backup_count
            )
        )

    def write_entry(self, entry: models.AuditLogEntry) -> bool:
        """Appends a JSONL audit record and enforces 0o600 permissions."""
        payload = entry.model_dump(mode="json")
        try:
            _sink_helpers.write_local_jsonl_record(
                self._lock, self._logger, self._handler, self._path, payload
            )
            return True
        except OSError as exc:
            raise exceptions.AuditLedgerError(
                f"Local audit write failed: {exc}"
            ) from exc

    def close(self) -> None:
        """Flushes and closes the underlying rotating file handler."""
        _sink_helpers.close_local_jsonl_sink(
            self._lock, self._logger, self._handler
        )

    def __enter__(self) -> LocalFileAuditAdapter:
        return self

    def __exit__(self, exc_t: Any, exc_v: Any, tb: Any) -> None:
        del exc_t, exc_v, tb
        self.close()
