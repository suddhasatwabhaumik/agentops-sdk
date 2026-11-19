# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Shared local JSONL, Cloud Logging, and BigQuery sink helpers."""

from __future__ import annotations

from collections.abc import Mapping
import contextlib
import json
import logging
from logging import handlers as log_handlers
import os
import pathlib
import sys
import threading
from typing import Any
import uuid

from google.api_core import retry as gcp_retry
from google.cloud import bigquery
from google.cloud import logging as cloud_logging
from typing_extensions import override

from agentops_sdk.config import settings
from agentops_sdk.domain import models
from agentops_sdk.domain import validation
from agentops_sdk.telemetry import otlp_exporters

_SYSTEM_ROOTS = ("/etc", "/private/etc", "/bin", "/sbin", "/usr", "/System")
_MAX_ROTATION_BYTES = 10_485_760
_CL_LOCK = threading.Lock()
_CL_CLIENTS: dict[str | None, Any] = {}


def reset_cloud_logging_clients() -> None:
    """Closes and clears cached Cloud Logging clients."""
    with _CL_LOCK:
        clients = list(_CL_CLIENTS.values())
        _CL_CLIENTS.clear()
    for cli in clients:
        close_fn = getattr(cli, "close", None)
        if callable(close_fn):
            with contextlib.suppress(Exception):
                close_fn()


def _get_cloud_logging_client(project_id: str | None) -> Any:
    from agentops_sdk.telemetry import provider

    with _CL_LOCK:
        cli = _CL_CLIENTS.get(project_id)
        if cli is None:
            cls: Any = cloud_logging.Client
            cli = cls(project=project_id)
            _CL_CLIENTS[project_id] = cli
    provider.register_reset_hook(reset_cloud_logging_clients)
    return cli


class _SecureRotatingFileHandler(log_handlers.RotatingFileHandler):
    """RotatingFileHandler enforcing O_NOFOLLOW, 0o600, and OSError raise."""

    @staticmethod
    def _secure_opener(path: str, flags: int) -> int:
        del flags
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
            0o600,
        )
        os.fchmod(fd, 0o600)
        return fd

    @override
    def _open(self) -> Any:
        return open(
            self.baseFilename,
            self.mode,
            encoding=self.encoding,
            errors=self.errors,
            opener=self._secure_opener,
        )

    @override
    def handleError(self, record: logging.LogRecord) -> None:
        _, exc, _ = sys.exc_info()
        if isinstance(exc, OSError):
            raise exc
        super().handleError(record)


def validate_local_sink_path(
    file_path: str | os.PathLike[str],
    base_dir: str | os.PathLike[str] | None = None,
) -> str:
    """Resolves a local file path and enforces realpath boundary checks."""
    raw = os.fspath(file_path).strip()
    if not raw:
        raise ValueError("file_path must be non-empty.")
    if ".." in pathlib.PurePath(raw.replace("\\", "/")).parts:
        raise ValueError(f"Path traversal prohibited in {raw!r}.")
    abs_target = os.path.abspath(raw)
    if os.path.islink(abs_target):
        raise ValueError(f"Symlink log target prohibited: {raw!r}.")
    parent_real = os.path.realpath(os.path.dirname(abs_target))
    target_real = os.path.join(parent_real, os.path.basename(abs_target))
    if base_dir is not None:
        real_base = os.path.realpath(os.fspath(base_dir))
        if (
            os.path.commonpath([real_base, target_real]) != real_base
            or target_real == real_base
        ):
            raise ValueError(f"Path {raw!r} escapes {base_dir!r}.")
    for root in _SYSTEM_ROOTS:
        if target_real == root or target_real.startswith(f"{root}/"):
            raise ValueError(f"Writing logs under {root} is prohibited.")
    return target_real


def open_local_jsonl_sink(
    file_path: str | os.PathLike[str],
    base_dir: str | os.PathLike[str] | None = None,
    max_bytes: int = _MAX_ROTATION_BYTES,
    backup_count: int = 5,
) -> tuple[str, logging.Handler, logging.Logger]:
    """Opens a path-validated rotating JSONL sink with 0o600 mode."""
    if not (0 < max_bytes <= _MAX_ROTATION_BYTES):
        raise ValueError(f"max_bytes must be in (0, {_MAX_ROTATION_BYTES}].")
    if backup_count < 0:
        raise ValueError("backup_count must be non-negative.")
    path = validate_local_sink_path(file_path, base_dir)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    h = _SecureRotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    h.setFormatter(logging.Formatter("%(message)s"))
    sink_log = logging.Logger(f"agentops.sink.{uuid.uuid4().hex}", logging.INFO)
    sink_log.addHandler(h)
    return path, h, sink_log


def write_local_jsonl_record(
    lock: threading.Lock,
    sink_log: logging.Logger,
    handler: logging.Handler,
    path: str,
    payload: Mapping[str, Any],
) -> None:
    """Appends a JSONL record under lock and preserves 0o600 permissions."""
    del path
    with lock:
        sink_log.info("%s", json.dumps(payload, default=str))
        handler.flush()


def close_local_jsonl_sink(
    lock: threading.Lock, sink_log: logging.Logger, handler: logging.Handler
) -> None:
    """Flushes and closes a local JSONL sink handler under lock."""
    with lock:
        sink_log.removeHandler(handler)
        handler.flush()
        handler.close()


def default_gcp_retry() -> gcp_retry.Retry:
    """Returns bounded exponential Retry for GCP telemetry/audit writes."""
    return gcp_retry.Retry(
        initial=0.5, maximum=10.0, multiplier=2.0, deadline=30.0
    )


def ensure_partitioned_bq_table(
    bq_client: Any,
    table_ref: str,
    schema: tuple[bigquery.SchemaField, ...],
    retry_policy: gcp_retry.Retry,
) -> None:
    """Creates a DAY-partitioned BigQuery table on `timestamp` if absent."""
    table = bigquery.Table(table_ref, schema=list(schema))
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY, field="timestamp"
    )
    bq_client.create_table(table, exists_ok=True, retry=retry_policy)


def _should_use_stdlib_only(
    mode: models.TelemetryMode,
    *,
    is_compliance_audit: bool,
    client: Any,
    project_id: str | None,
) -> bool:
    if mode == models.TelemetryMode.OTLP:
        return True
    if mode != models.TelemetryMode.OFF or client is not None:
        return False
    if not is_compliance_audit:
        return True
    proj, _ = settings.resolve_gcp_project_and_location(
        project_id, require_project=False
    )
    return proj is None


def write_cloud_logging_record(
    payload: dict[str, Any],
    *,
    project_id: str | None,
    log_name: str,
    client: Any,
    severity: str,
    stdlib_logger: logging.Logger,
    log_message: str,
    is_compliance_audit: bool = False,
) -> None:
    """Routes structured record to OTLP stdlib logger and/or Cloud Logging."""
    mode = otlp_exporters.resolve_active_mode()
    safe_msg = validation.sanitize_log_field(log_message)
    safe_name = validation.sanitize_log_field(log_name, max_length=256)
    if mode in (models.TelemetryMode.OTLP, models.TelemetryMode.HYBRID):
        stdlib_logger.info("%s", safe_msg, extra={"json_fields": payload})
    if _should_use_stdlib_only(
        mode,
        is_compliance_audit=is_compliance_audit,
        client=client,
        project_id=project_id,
    ):
        if mode != models.TelemetryMode.OTLP:
            stdlib_logger.info("%s", safe_msg, extra={"json_fields": payload})
        return
    proj, _ = settings.resolve_gcp_project_and_location(
        project_id, require_project=client is None
    )
    (client or _get_cloud_logging_client(proj)).logger(safe_name).log_struct(
        payload, severity=severity
    )


class PartitionedBQWriter:
    """Thread-safe BigQuery JSON row writer with lazy DAY-partition DDL."""

    def __init__(
        self,
        project_id: str | None,
        dataset_id: str,
        table_id: str,
        client: Any,
        auto_create_table: bool,
    ) -> None:
        self._project_id, self._client = project_id, client
        self._owns_client = client is None
        v_id = settings.validate_gcp_identifier
        self._dataset = v_id(dataset_id, "dataset_id")
        self._table = v_id(table_id, "table_id")
        self._auto_create = auto_create_table
        self._verified: set[str] = set()
        self._lock = threading.Lock()

    def _get_client_and_ref(self) -> tuple[Any, str, str]:
        proj, _ = settings.resolve_gcp_project_and_location(
            self._project_id, require_project=self._client is None
        )
        r_proj = proj or getattr(self._client, "project", None) or "local"
        if self._client is None:
            self._client = bigquery.Client(project=r_proj)
        return self._client, r_proj, f"{r_proj}.{self._dataset}.{self._table}"

    def insert_row(
        self, row: dict[str, Any], schema: tuple[bigquery.SchemaField, ...]
    ) -> list[Any]:
        """Ensures partitioned table once and inserts a single JSON row."""
        retry_pol = default_gcp_retry()
        with self._lock:
            bq, r_proj, t_ref = self._get_client_and_ref()
            if self._auto_create and t_ref not in self._verified:
                ds_fn = getattr(bq, "create_dataset", None)
                if callable(ds_fn):
                    ds_fn(
                        f"{r_proj}.{self._dataset}",
                        exists_ok=True,
                        retry=retry_pol,
                    )
                ensure_partitioned_bq_table(bq, t_ref, schema, retry_pol)
                self._verified.add(t_ref)
        return list(bq.insert_rows_json(t_ref, [row], retry=retry_pol) or ())

    def close(self) -> None:
        """Closes internally managed BigQuery client and clears cache."""
        with self._lock:
            cli = self._client if self._owns_client else None
            if self._owns_client:
                self._client = None
            self._verified.clear()
        if cli is not None and callable(fn := getattr(cli, "close", None)):
            with contextlib.suppress(Exception):
                fn()
