# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Google Cloud Trace, Cloud Monitoring, Logging, BigQuery, and GCS sinks."""

from __future__ import annotations

import contextlib
import json
import logging
import re
import threading
from typing import Any
import uuid

from google.cloud import bigquery
import google.cloud.storage as gcs_storage
from opentelemetry.exporter import cloud_monitoring
from opentelemetry.exporter import cloud_trace
from opentelemetry.sdk.metrics import export as sdk_metric_export
from opentelemetry.sdk.trace import export as sdk_trace_export

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import models
from agentops_sdk.domain import validation
from agentops_sdk.telemetry import _sink_helpers

logger = logging.getLogger(__name__)
_SAFE_SEG_RE = re.compile(r"[^A-Za-z0-9._-]+")
default_gcp_retry = _sink_helpers.default_gcp_retry
ensure_partitioned_bq_table = _sink_helpers.ensure_partitioned_bq_table
_BQ_SPEC = (
    "case_id:STRING:REQUIRED use_case_name:STRING:REQUIRED session_id:STRING "
    "user_id:STRING trace_id:STRING span_id:STRING "
    "transactions_processed:INT64 stages:JSON total_latency_ms:FLOAT64 "
    "success:BOOL error_message:STRING business_kpi:STRING "
    "pl_impact_line:STRING metadata:JSON timestamp:TIMESTAMP:REQUIRED"
)


def _bq_field(spec: str) -> bigquery.SchemaField:
    name, ftype, *mode = spec.split(":")
    return bigquery.SchemaField(
        name, ftype, mode=mode[0] if mode else "NULLABLE"
    )


USE_CASE_BQ_SCHEMA: tuple[bigquery.SchemaField, ...] = tuple(
    _bq_field(spec) for spec in _BQ_SPEC.split()
)


def sanitize_gcs_segment(value: str, fallback: str = "unknown") -> str:
    """Sanitizes a GCS path segment against '/' and '..' traversal."""
    cleaned = value.replace("..", "-").replace("/", "-").replace("\\", "-")
    cleaned = _SAFE_SEG_RE.sub("-", cleaned.strip()).strip(".-")
    return cleaned[:128] if cleaned else fallback


def _require_proj(project_id: str | None) -> str:
    proj, _ = settings.resolve_gcp_project_and_location(
        project_id, require_project=True
    )
    return str(proj)


def create_gcp_span_processor(
    project_id: str | None = None,
) -> sdk_trace_export.BatchSpanProcessor:
    """Creates a CloudTraceSpanExporter BatchSpanProcessor."""
    proj = _require_proj(project_id)
    try:
        exp_cls: Any = cloud_trace.CloudTraceSpanExporter
        return sdk_trace_export.BatchSpanProcessor(exp_cls(project_id=proj))
    except Exception as exc:
        msg = f"Cloud Trace init failed: {exc}"
        raise exceptions.TelemetryExportError(msg) from exc


def create_gcp_metric_reader(
    project_id: str | None = None, export_interval_millis: float = 60000.0
) -> sdk_metric_export.PeriodicExportingMetricReader:
    """Creates a PeriodicExportingMetricReader backed by Cloud Monitoring."""
    proj = _require_proj(project_id)
    try:
        exp = cloud_monitoring.CloudMonitoringMetricsExporter(project_id=proj)
        return sdk_metric_export.PeriodicExportingMetricReader(
            exp, export_interval_millis=export_interval_millis
        )
    except Exception as exc:
        msg = f"Monitoring init failed: {exc}"
        raise exceptions.TelemetryExportError(msg) from exc


class CloudLoggingTelemetryExporter:
    """Exports case telemetry to Cloud Logging in GCP mode or stdlib OTLP."""

    def __init__(
        self,
        project_id: str | None = None,
        log_name: str = "agentops-use-case-telemetry",
        client: Any = None,
    ) -> None:
        self._project_id, self._log_name = project_id, log_name
        self._client = client

    def export(self, record: models.UseCaseTelemetry) -> bool:
        """Exports a UseCaseTelemetry payload to Cloud Logging or OTLP."""
        payload = {
            **record.model_dump(mode="json"),
            "event_type": "use_case_telemetry",
        }
        if record.error_message:
            payload["error_message"] = validation.scrub_sensitive_text(
                record.error_message
            )
        try:
            _sink_helpers.write_cloud_logging_record(
                payload,
                project_id=self._project_id,
                log_name=self._log_name,
                client=self._client,
                severity="INFO" if record.success else "ERROR",
                stdlib_logger=logger,
                log_message=f"Case={record.case_id}",
                is_compliance_audit=False,
            )
            return True
        except Exception as exc:
            raise exceptions.TelemetryExportError(
                f"Cloud Logging failed: {exc}"
            ) from exc


class BigQueryTelemetryExporter:
    """Exports UseCaseTelemetry rows to DAY-partitioned BigQuery storage."""

    def __init__(
        self,
        project_id: str | None = None,
        dataset_id: str = "agentops_telemetry",
        table_id: str = "use_case_telemetry",
        client: Any = None,
        auto_create_table: bool = True,
    ) -> None:
        self._writer = _sink_helpers.PartitionedBQWriter(
            project_id, dataset_id, table_id, client, auto_create_table
        )

    def export(self, record: models.UseCaseTelemetry) -> bool:
        """Inserts a UseCaseTelemetry row into BigQuery with Retry."""
        try:
            row = {
                **record.model_dump(mode="json"),
                "stages": json.dumps(record.stages),
                "metadata": json.dumps(record.metadata, default=str),
                "error_message": validation.scrub_sensitive_text(
                    record.error_message or ""
                )
                or None,
            }
            errors = self._writer.insert_row(row, USE_CASE_BQ_SCHEMA)
            if errors:
                raise exceptions.TelemetryExportError(f"BQ errors: {errors}")
            return True
        except exceptions.TelemetryExportError:
            raise
        except Exception as exc:
            raise exceptions.TelemetryExportError(
                f"BQ export failed: {exc}"
            ) from exc

    def close(self) -> None:
        """Closes internally managed BigQuery client."""
        self._writer.close()

    def __enter__(self) -> BigQueryTelemetryExporter:
        return self

    def __exit__(self, exc_t: Any, exc_v: Any, tb: Any) -> None:
        del exc_t, exc_v, tb
        self.close()


class BatchedGCSTelemetryExporter:
    """Buffers UseCaseTelemetry NDJSON lines and uploads batches to GCS."""

    def __init__(
        self,
        bucket_name: str,
        prefix: str = "use-case-telemetry",
        batch_size: int = 50,
        project_id: str | None = None,
        client: Any = None,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive.")
        self._bucket = settings.validate_gcp_identifier(
            bucket_name, "bucket_name"
        )
        self._prefix, self._batch_size = prefix, batch_size
        self._project_id, self._client = project_id, client
        self._owns_client = client is None
        self._lock = threading.Lock()
        self._buffer: list[models.UseCaseTelemetry] = []

    def _get_client(self) -> Any:
        with self._lock:
            if self._client is None:
                proj = settings.resolve_gcp_project_and_location(
                    self._project_id
                )[0]
                self._client = gcs_storage.Client(project=proj)
            return self._client

    def export(self, record: models.UseCaseTelemetry) -> bool:
        """Buffers a record and uploads to GCS once batch_size is reached."""
        with self._lock:
            self._buffer.append(record)
            if len(self._buffer) < self._batch_size:
                return True
            batch, self._buffer = list(self._buffer), []
        self._upload(batch)
        return True

    def flush(self) -> bool:
        """Uploads any remaining buffered NDJSON telemetry records to GCS."""
        with self._lock:
            if not self._buffer:
                return True
            batch, self._buffer = list(self._buffer), []
        self._upload(batch)
        return True

    def close(self) -> None:
        """Flushes buffered records and closes internally owned GCS client."""
        try:
            self.flush()
        finally:
            with self._lock:
                cli = self._client if self._owns_client else None
                if self._owns_client:
                    self._client = None
            if cli is not None:
                close_fn = getattr(cli, "close", None)
                if callable(close_fn):
                    with contextlib.suppress(Exception):
                        close_fn()

    def __enter__(self) -> BatchedGCSTelemetryExporter:
        return self

    def __exit__(self, exc_t: Any, exc_v: Any, tb: Any) -> None:
        del exc_t, exc_v, tb
        self.close()

    @staticmethod
    def _serialize_record(record: models.UseCaseTelemetry) -> str:
        row = record.model_dump(mode="json")
        if record.error_message:
            row["error_message"] = validation.scrub_sensitive_text(
                record.error_message
            )
        return json.dumps(row, default=str)

    def _upload(self, batch: list[models.UseCaseTelemetry]) -> None:
        try:
            gcs = self._get_client()
            segs = [
                sanitize_gcs_segment(p, "telemetry")
                for p in self._prefix.strip("/").split("/")
                if p and p != "."
            ]
            u_n = sanitize_gcs_segment(batch[0].use_case_name, "use-case")
            c_id = sanitize_gcs_segment(batch[0].case_id, "case")
            day = sanitize_gcs_segment(batch[0].timestamp[:10], "1970-01-01")
            leaf = f"{u_n}/{day}/{c_id}_{uuid.uuid4().hex[:8]}.ndjson"
            key = f"{'/'.join(segs)}/{leaf}" if segs else leaf
            body = "\n".join(self._serialize_record(r) for r in batch)
            gcs.bucket(self._bucket).blob(key).upload_from_string(
                f"{body}\n",
                content_type="application/x-ndjson",
                retry=default_gcp_retry(),
            )
        except Exception as exc:
            raise exceptions.TelemetryExportError(
                f"GCS upload failed: {exc}"
            ) from exc
