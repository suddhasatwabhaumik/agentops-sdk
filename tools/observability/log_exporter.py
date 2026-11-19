# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Cloud Logging export, backfill, and synthetic validation helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import datetime
import errno
import json
import logging
import os
from typing import IO, Any

from google.api_core import exceptions as gcp_exceptions
from google.cloud import bigquery
from google.cloud import logging as cloud_logging

logger = logging.getLogger("log_router_admin")

DEFAULT_LOG_FILTER = (
    'logName:"logs/agentops-finops-telemetry" OR '
    'logName:"logs/agentops-usecase-telemetry" OR '
    'jsonPayload.event_type="finops_llm_telemetry" OR '
    'jsonPayload.event_type="use_case_telemetry"'
)

_EXPORT_TABLE_SCHEMA = (
    bigquery.SchemaField("log_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("severity", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("log_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("trace_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("span_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("payload", "STRING", mode="NULLABLE"),
)


def _entry_to_record(entry: Any) -> dict[str, Any]:
    payload = (
        entry.payload
        if isinstance(entry.payload, dict)
        else {"message": str(entry.payload)}
    )
    ts = (
        entry.timestamp.isoformat()
        if entry.timestamp
        else datetime.datetime.now(datetime.UTC).isoformat()
    )
    return {
        "log_id": entry.insert_id or "",
        "timestamp": ts,
        "severity": entry.severity or "INFO",
        "log_name": entry.log_name or "",
        "trace_id": entry.trace or "",
        "span_id": entry.span_id or "",
        "payload": payload,
    }


def fetch_log_entries(
    logging_client: Any,
    filter_expr: str | None = None,
    hours: int = 24,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Fetches log entries from Cloud Logging within the given time window."""
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        hours=hours
    )
    time_filter = f'timestamp >= "{cutoff.isoformat()}"'
    base_filter = filter_expr or DEFAULT_LOG_FILTER
    combined_filter = f"({base_filter}) AND {time_filter}"
    logger.info("Querying Cloud Logging with filter: %s", combined_filter)
    entries = logging_client.list_entries(
        filter_=combined_filter,
        max_results=limit,
        order_by=cloud_logging.DESCENDING,
    )
    records = [_entry_to_record(entry) for entry in entries]
    logger.info("Retrieved %d log records from Cloud Logging.", len(records))
    return records


def _open_secure_output(output_file: str) -> IO[str]:
    clean_path = output_file.strip()
    if not clean_path or "\x00" in clean_path:
        raise ValueError(f"Invalid output_file path: {output_file!r}")
    if os.path.islink(clean_path):
        raise ValueError(f"Symlink output target prohibited: {output_file!r}")
    parent = os.path.dirname(clean_path)
    if parent:
        os.makedirs(parent, mode=0o700, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(clean_path, flags, 0o600)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(
                f"Symlink output target prohibited: {output_file!r}"
            ) from exc
        raise
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        return os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise


def write_jsonl_file(
    records: Sequence[Mapping[str, Any]],
    output_file: str,
) -> None:
    """Writes exported log records as UTF-8 JSON Lines safely (mode 0o600)."""
    with _open_secure_output(output_file) as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    if not hasattr(os, "fchmod"):
        os.chmod(output_file.strip(), 0o600)
    logger.info("Wrote %d records to %r.", len(records), output_file)


def export_to_bigquery(
    bq_client: Any,
    project_id: str,
    dataset_id: str,
    records: Sequence[Mapping[str, Any]],
) -> int:
    """Streams exported log records into a day-partitioned BigQuery table."""
    table_ref = f"{project_id}.{dataset_id}.exported_cloud_logs"
    try:
        table = bigquery.Table(table_ref, schema=list(_EXPORT_TABLE_SCHEMA))
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="timestamp",
        )
        bq_client.create_table(table, exists_ok=True)
        rows = [
            {
                "log_id": r["log_id"],
                "timestamp": r["timestamp"],
                "severity": r["severity"],
                "log_name": r["log_name"],
                "trace_id": r["trace_id"],
                "span_id": r["span_id"],
                "payload": json.dumps(r["payload"]),
            }
            for r in records
        ]
        errs = bq_client.insert_rows_json(table_ref, rows)
        if errs:
            logger.error("BigQuery export reported errors: %s", errs)
            return 0
        logger.info("Streamed %d log records into %r.", len(records), table_ref)
        return len(records)
    except gcp_exceptions.GoogleAPICallError as exc:
        logger.error("BigQuery manual export failed: %s", exc)
        return 0


def export_to_gcs(
    storage_client: Any,
    bucket_name: str,
    records: Sequence[Mapping[str, Any]],
) -> int:
    """Uploads exported log records to GCS as newline-delimited JSON."""
    now = datetime.datetime.now(datetime.UTC)
    date_part = now.strftime("%Y-%m-%d")
    ts_part = now.strftime("%Y%m%d_%H%M%S")
    object_name = f"manual_exports/{date_part}/cloud_logs_{ts_part}.jsonl"
    try:
        blob = storage_client.bucket(bucket_name).blob(object_name)
        ndjson = "\n".join(json.dumps(r) for r in records) + "\n"
        blob.upload_from_string(ndjson, content_type="application/x-ndjson")
        logger.info(
            "Uploaded %d records to gs://%s/%s.",
            len(records),
            bucket_name,
            object_name,
        )
        return len(records)
    except gcp_exceptions.GoogleAPICallError as exc:
        logger.error("GCS manual export failed: %s", exc)
        return 0


def emit_synthetic_test_log(
    logging_client: Any,
    project_id: str,
) -> dict[str, Any]:
    """Emits a synthetic FinOps LLM telemetry record to Cloud Logging."""
    gcp_logger = logging_client.logger("agentops-finops-telemetry")
    payload: dict[str, Any] = {
        "event_type": "finops_llm_telemetry",
        "business": {
            "project": project_id,
            "environment": "sandbox",
            "kpi_id": "test-observability-validation",
            "pl_impact_line": "ai-platform-opex",
            "agent_role": "synthetic-validation-runner",
            "user_id": "verification-tester",
            "session_id": "test-session-001",
        },
        "llm": {
            "function_name": "synthetic_model_inference",
            "model_name": "gemini-1.5-pro",
            "finish_reason": "STOP",
            "input_tokens": 128,
            "output_tokens": 256,
            "total_tokens": 384,
            "cached_tokens": 0,
            "is_cached": False,
            "prompt_chars": 512,
            "response_chars": 1024,
            "latency_ms": 245.8,
            "tokens_per_sec": 1041.5,
            "status": "SUCCESS",
        },
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    gcp_logger.log_struct(payload, severity="INFO")
    logger.info(
        "Emitted synthetic LLM telemetry log to 'agentops-finops-telemetry'."
    )
    return payload
