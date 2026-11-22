#!/usr/bin/env python3
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Log Router Administration & Telemetry Export Utility for AgentOps."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import logging
from typing import Any

from google.cloud import bigquery
from google.cloud import exceptions as cloud_exceptions
from google.cloud import logging as cloud_logging
from google.cloud.logging_v2.services import config_service_v2
import google.cloud.storage as storage

from agentops_sdk.config import settings
from tools.observability import _sink_ops
from tools.observability import log_exporter

logger = logging.getLogger("log_router_admin")

DEFAULT_LOG_FILTER = log_exporter.DEFAULT_LOG_FILTER


class LogRouterManager:
    """Manages GCP Log Router sinks, IAM grants, and manual log exports."""

    def __init__(
        self,
        project_id: str | None = None,
        region: str | None = None,
        dataset_id: str = "agentops_observability",
        bucket_name: str | None = None,
        *,
        logging_client: Any | None = None,
        bq_client: Any | None = None,
        storage_client: Any | None = None,
        config_client: Any | None = None,
    ) -> None:
        proj, loc = settings.resolve_gcp_project_and_location(
            project_id, region, require_project=True
        )
        self.project_id: str = str(proj)
        self.region: str = str(loc)
        self.dataset_id: str = str(
            settings.validate_gcp_identifier(dataset_id, "dataset_id")
        )
        self.bucket_name: str = (
            bucket_name or f"{self.project_id}-agentops-observability-logs"
        )
        log_cls: Any = cloud_logging.Client
        self.logging_client: Any = logging_client or log_cls(
            project=self.project_id
        )
        self.bq_client: Any = bq_client or bigquery.Client(
            project=self.project_id
        )
        self.storage_client: Any = storage_client or storage.Client(
            project=self.project_id
        )
        self._config_client = config_client

    def ensure_bigquery_dataset(self) -> str:
        """Ensures the BigQuery analytics dataset exists in the region."""
        dataset_ref = f"{self.project_id}.{self.dataset_id}"
        try:
            self.bq_client.get_dataset(dataset_ref)
            logger.info("BigQuery dataset %r already exists.", dataset_ref)
        except cloud_exceptions.NotFound:
            dataset = bigquery.Dataset(dataset_ref)
            dataset.location = self.region
            dataset.description = (
                "AgentOps Observability & FinOps LLM analytics dataset"
            )
            self.bq_client.create_dataset(dataset, exists_ok=True)
            logger.info(
                "Created BigQuery dataset %r in %r.", dataset_ref, self.region
            )
        return self.dataset_id

    def ensure_gcs_bucket(self) -> str:
        """Ensures the GCS bucket for log archives exists."""
        try:
            self.storage_client.get_bucket(self.bucket_name)
            logger.info("GCS bucket gs://%s already exists.", self.bucket_name)
        except cloud_exceptions.NotFound:
            bucket = self.storage_client.bucket(self.bucket_name)
            bucket.storage_class = "STANDARD"
            bucket.iam_configuration.uniform_bucket_level_access_enabled = True
            bucket.iam_configuration.public_access_prevention = "enforced"
            bucket.versioning_enabled = True
            self.storage_client.create_bucket(bucket, location=self.region)
            logger.info(
                "Created GCS bucket gs://%s in %r.",
                self.bucket_name,
                self.region,
            )
        return self.bucket_name

    def _resolve_gapic_config_client(self) -> Any | None:
        if self._config_client is not None:
            return self._config_client
        sinks_api = getattr(self.logging_client, "sinks_api", None)
        gapic = getattr(sinks_api, "_gapic_api", None)
        if isinstance(gapic, config_service_v2.ConfigServiceV2Client):
            return gapic
        return None

    def setup_bigquery_sink(
        self,
        sink_name: str = "agentops-finops-llm-bq-sink",
        filter_expr: str | None = None,
    ) -> dict[str, Any]:
        """Creates or updates the partitioned BigQuery Log Router sink."""
        self.ensure_bigquery_dataset()
        destination = (
            f"bigquery.googleapis.com/projects/{self.project_id}/"
            f"datasets/{self.dataset_id}"
        )
        log_filter = filter_expr or DEFAULT_LOG_FILTER
        gapic = self._resolve_gapic_config_client()
        if gapic is not None:
            writer_id = _sink_ops.upsert_sink_via_gapic(
                gapic,
                self.project_id,
                sink_name,
                log_filter,
                destination,
                use_partitioned_tables=True,
            )
        else:
            writer_id = _sink_ops.upsert_sink_via_client(
                self.logging_client, sink_name, log_filter, destination
            )
        logger.info("Sink %r writer identity: %s", sink_name, writer_id)
        self._grant_bigquery_dataset_access(writer_id)
        return {
            "sink_name": sink_name,
            "destination": destination,
            "writer_identity": writer_id,
            "filter": log_filter,
        }

    def setup_gcs_sink(
        self,
        sink_name: str = "agentops-finops-llm-gcs-sink",
        filter_expr: str | None = None,
    ) -> dict[str, Any]:
        """Creates or updates the GCS Log Router sink."""
        self.ensure_gcs_bucket()
        destination = f"storage.googleapis.com/{self.bucket_name}"
        log_filter = filter_expr or DEFAULT_LOG_FILTER
        writer_id = _sink_ops.upsert_sink_via_client(
            self.logging_client, sink_name, log_filter, destination
        )
        logger.info("Sink %r writer identity: %s", sink_name, writer_id)
        self._grant_gcs_bucket_access(writer_id)
        return {
            "sink_name": sink_name,
            "destination": destination,
            "writer_identity": writer_id,
            "filter": log_filter,
        }

    def _grant_bigquery_dataset_access(
        self, writer_identity: str | None
    ) -> None:
        _sink_ops.grant_bigquery_dataset_access(
            self.bq_client, self.dataset_id, writer_identity
        )

    def _grant_gcs_bucket_access(self, writer_identity: str | None) -> None:
        _sink_ops.grant_gcs_bucket_access(
            self.storage_client, self.bucket_name, writer_identity
        )

    def list_sinks(self) -> list[dict[str, Any]]:
        """Lists existing logging sinks for the target project."""
        return [
            {
                "name": sink.name,
                "destination": sink.destination,
                "filter": sink.filter_,
                "writer_identity": getattr(sink, "writer_identity", "") or "",
            }
            for sink in self.logging_client.list_sinks()
        ]

    def export_logs(
        self,
        filter_expr: str | None = None,
        hours: int = 24,
        limit: int = 500,
        target_bq: bool = True,
        target_gcs: bool = True,
        local_output_file: str | None = None,
    ) -> dict[str, Any]:
        """Queries Cloud Logging and exports logs to BigQuery/GCS/JSONL."""
        records = log_exporter.fetch_log_entries(
            self.logging_client,
            filter_expr=filter_expr,
            hours=hours,
            limit=limit,
        )
        stats: dict[str, Any] = {
            "retrieved_count": len(records),
            "bq_exported": 0,
            "gcs_exported": 0,
            "local_file": local_output_file,
        }
        if local_output_file:
            log_exporter.write_jsonl_file(records, local_output_file)
        if not records:
            return stats
        if target_bq:
            self.ensure_bigquery_dataset()
            stats["bq_exported"] = log_exporter.export_to_bigquery(
                self.bq_client, self.project_id, self.dataset_id, records
            )
        if target_gcs:
            self.ensure_gcs_bucket()
            stats["gcs_exported"] = log_exporter.export_to_gcs(
                self.storage_client, self.bucket_name, records
            )
        return stats

    def emit_synthetic_test_log(self) -> dict[str, Any]:
        """Emits a sample structured FinOps telemetry log to Cloud Logging."""
        return log_exporter.emit_synthetic_test_log(
            self.logging_client, self.project_id
        )


def _print_setup_dry_run(args: argparse.Namespace) -> None:
    bucket = args.bucket or f"{args.project}-agentops-observability-logs"
    bq_dest = (
        f"bigquery.googleapis.com/projects/{args.project}/"
        f"datasets/{args.dataset}"
    )
    gcs_dest = f"storage.googleapis.com/{bucket}"
    print(f"[DRY-RUN] BigQuery Sink ({args.bq_sink}) -> {bq_dest}")
    print(f"[DRY-RUN] GCS Sink ({args.gcs_sink}) -> {gcs_dest}")


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint for Log Router provisioning and telemetry export."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    default_proj, default_loc = settings.resolve_gcp_project_and_location(
        require_project=False
    )
    parser = _sink_ops.build_cli_parser(default_proj, default_loc)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.project:
        parser.error("--project or GOOGLE_CLOUD_PROJECT is required.")
    if (
        args.command == "export"
        and getattr(args, "destination", None) == "file"
        and not (args.output_file or "").strip()
    ):
        parser.error("--destination file requires --output-file.")
    if args.command == "setup" and getattr(args, "dry_run", False):
        _print_setup_dry_run(args)
        return
    manager = LogRouterManager(
        project_id=args.project,
        region=args.region,
        dataset_id=args.dataset,
        bucket_name=args.bucket,
    )
    _sink_ops.dispatch_cli_command(manager, args)


if __name__ == "__main__":
    main()
