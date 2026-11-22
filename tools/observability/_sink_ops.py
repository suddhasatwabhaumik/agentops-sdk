# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Internal Log Router sink upsert, IAM, and CLI dispatch helpers."""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from google.api_core import exceptions as gcp_exceptions
from google.cloud import bigquery
from google.cloud.logging_v2.types import logging_config
from google.protobuf import field_mask_pb2

logger = logging.getLogger("log_router_admin")


def require_writer_identity(writer_identity: str | None) -> str:
    """Validates that a Log Sink writer_identity is non-empty."""
    cleaned = (writer_identity or "").strip()
    if not cleaned:
        raise ValueError("Sink writer_identity must not be None or empty.")
    return cleaned


def add_member_to_bindings(
    bindings: list[dict[str, Any]],
    role: str,
    member: str,
) -> None:
    """Mutates IAM Policy v3 bindings idempotently for an unconditional role."""
    for binding in bindings:
        if binding.get("role") == role and binding.get("condition") is None:
            members = set(binding.get("members", ()))
            members.add(member)
            binding["members"] = members
            return
    bindings.append({"role": role, "members": {member}})


def _build_gapic_sink(
    sink_name: str,
    log_filter: str,
    destination: str,
    *,
    use_partitioned_tables: bool,
) -> logging_config.LogSink:
    bq_opts = (
        logging_config.BigQueryOptions(use_partitioned_tables=True)
        if use_partitioned_tables
        else None
    )
    return logging_config.LogSink(
        name=sink_name,
        filter=log_filter,
        destination=destination,
        bigquery_options=bq_opts,
    )


def _update_gapic_sink(
    gapic: Any,
    full_name: str,
    sink_name: str,
    sink_pb: Any,
    *,
    use_partitioned_tables: bool,
) -> Any:
    paths = ["destination", "filter"]
    if use_partitioned_tables:
        paths.append("bigquery_options")
    update_req = logging_config.UpdateSinkRequest(
        sink_name=full_name,
        sink=sink_pb,
        unique_writer_identity=True,
        update_mask=field_mask_pb2.FieldMask(paths=paths),
    )
    res = gapic.update_sink(request=update_req)
    logger.info("Updated Log Sink %r.", sink_name)
    return res


def upsert_sink_via_gapic(
    gapic: Any,
    project_id: str,
    sink_name: str,
    log_filter: str,
    destination: str,
    *,
    use_partitioned_tables: bool,
) -> str:
    """Creates or updates a LogSink via ConfigServiceV2Client."""
    sink_pb = _build_gapic_sink(
        sink_name,
        log_filter,
        destination,
        use_partitioned_tables=use_partitioned_tables,
    )
    full_name = f"projects/{project_id}/sinks/{sink_name}"
    try:
        gapic.get_sink(
            request=logging_config.GetSinkRequest(sink_name=full_name)
        )
        res = _update_gapic_sink(
            gapic,
            full_name,
            sink_name,
            sink_pb,
            use_partitioned_tables=use_partitioned_tables,
        )
    except gcp_exceptions.NotFound:
        create_req = logging_config.CreateSinkRequest(
            parent=f"projects/{project_id}",
            sink=sink_pb,
            unique_writer_identity=True,
        )
        res = gapic.create_sink(request=create_req)
        logger.info("Created Log Sink %r.", sink_name)
    return require_writer_identity(getattr(res, "writer_identity", None))


def upsert_sink_via_client(
    logging_client: Any,
    sink_name: str,
    log_filter: str,
    destination: str,
) -> str:
    """Creates or updates a Sink via google.cloud.logging.Client.sink()."""
    sink = logging_client.sink(
        name=sink_name,
        filter_=log_filter,
        destination=destination,
    )
    if sink.exists():
        sink.reload()
        sink.filter_ = log_filter
        sink.destination = destination
        sink.update(unique_writer_identity=True)
        logger.info("Updated existing Log Sink %r.", sink_name)
    else:
        sink.create(unique_writer_identity=True)
        sink.reload()
        logger.info("Created Log Sink %r.", sink_name)
    return require_writer_identity(sink.writer_identity)


def grant_bigquery_dataset_access(
    bq_client: Any,
    dataset_id: str,
    writer_identity: str | None,
) -> None:
    """Grants WRITER access to the sink service account on the dataset."""
    clean_id = require_writer_identity(writer_identity)
    email = clean_id.removeprefix("serviceAccount:")
    dataset = bq_client.get_dataset(dataset_id)
    entries = list(dataset.access_entries)
    has_access = any(
        e.entity_id == email and e.role in ("WRITER", "OWNER") for e in entries
    )
    if not has_access:
        entries.append(
            bigquery.AccessEntry(
                role="WRITER", entity_type="userByEmail", entity_id=email
            )
        )
        dataset.access_entries = entries
        bq_client.update_dataset(dataset, ["access_entries"])
        logger.info("Granted WRITER on dataset %r to %r.", dataset_id, email)


def grant_gcs_bucket_access(
    storage_client: Any,
    bucket_name: str,
    writer_identity: str | None,
) -> None:
    """Grants roles/storage.objectCreator using IAM Policy v3 bindings."""
    clean_id = require_writer_identity(writer_identity)
    member = (
        clean_id
        if clean_id.startswith("serviceAccount:")
        else f"serviceAccount:{clean_id}"
    )
    role = "roles/storage.objectCreator"
    try:
        bucket = storage_client.bucket(bucket_name)
        policy = bucket.get_iam_policy(requested_policy_version=3)
        if hasattr(policy, "bindings") and isinstance(policy.bindings, list):
            add_member_to_bindings(policy.bindings, role, member)
        elif isinstance(policy, dict):
            members = set(policy.get(role, set()))
            members.add(member)
            policy[role] = members
        else:
            raise TypeError(f"Unsupported IAM policy type: {type(policy)}")
        bucket.set_iam_policy(policy)
        logger.info("Granted %s on gs://%s to %s.", role, bucket_name, member)
    except gcp_exceptions.GoogleAPICallError as exc:
        logger.warning("Bucket IAM update failed: %s", exc)


_DEST_TARGETS: dict[str, tuple[bool, bool]] = {
    "bq": (True, False),
    "gcs": (False, True),
    "file": (False, False),
    "both": (True, True),
}


def _add_common_cli_args(
    parser: argparse.ArgumentParser,
    default_project: str | None,
    default_region: str,
    *,
    is_subparser: bool = False,
) -> None:
    sup: Any = argparse.SUPPRESS
    proj_def: Any = sup if is_subparser else default_project
    reg_def: Any = sup if is_subparser else default_region
    ds_def: Any = sup if is_subparser else "agentops_observability"
    bkt_def: Any = sup if is_subparser else None
    add = parser.add_argument
    add("--project", "--project-id", dest="project", default=proj_def)
    add("--region", dest="region", default=reg_def)
    add("--dataset", "--dataset-id", dest="dataset", default=ds_def)
    add("--bucket", "--bucket-name", dest="bucket", default=bkt_def)


def build_cli_parser(
    default_project: str | None,
    default_region: str,
) -> argparse.ArgumentParser:
    """Constructs the CLI ArgumentParser for Log Router management."""
    parser = argparse.ArgumentParser(
        description="GCP Log Router Setup and Observability Export Utility"
    )
    _add_common_cli_args(parser, default_project, default_region)
    sub = parser.add_subparsers(dest="command", required=True)
    setup_cmd = sub.add_parser("setup", help="Configure BQ and GCS Log Sinks")
    setup_cmd.add_argument("--bq-sink", default="agentops-finops-llm-bq-sink")
    setup_cmd.add_argument("--gcs-sink", default="agentops-finops-llm-gcs-sink")
    setup_cmd.add_argument("--dry-run", action="store_true")
    list_cmd = sub.add_parser(
        "list-sinks", help="List existing Log Router sinks"
    )
    exp = sub.add_parser("export", help="Export logs to BQ/GCS/JSONL")
    exp.add_argument("--hours", type=int, default=24)
    exp.add_argument("--limit", type=int, default=500)
    exp.add_argument("--filter", default=None)
    exp.add_argument("--no-bq", action="store_true")
    exp.add_argument("--no-gcs", action="store_true")
    exp.add_argument(
        "--destination",
        "--dest",
        dest="destination",
        choices=("bq", "gcs", "file", "both"),
        default=None,
    )
    exp.add_argument("--output-file", default=None)
    verify_cmd = sub.add_parser("verify", help="Emit synthetic test log")
    for sub_p in (setup_cmd, list_cmd, exp, verify_cmd):
        _add_common_cli_args(
            sub_p, default_project, default_region, is_subparser=True
        )
    return parser


def dispatch_cli_command(manager: Any, args: argparse.Namespace) -> None:
    """Executes the selected CLI subcommand against LogRouterManager."""
    if args.command == "setup":
        bq_res = manager.setup_bigquery_sink(sink_name=args.bq_sink)
        gcs_res = manager.setup_gcs_sink(sink_name=args.gcs_sink)
        print(f"[OK] BigQuery Sink: {bq_res['sink_name']}")
        print(f"[OK] GCS Sink: {gcs_res['sink_name']}")
    elif args.command == "list-sinks":
        for sink in manager.list_sinks():
            print(json.dumps(sink))
    elif args.command == "export":
        dest = str(getattr(args, "destination", "") or "")
        target_bq, target_gcs = _DEST_TARGETS.get(
            dest, (not args.no_bq, not args.no_gcs)
        )
        stats = manager.export_logs(
            filter_expr=args.filter,
            hours=args.hours,
            limit=args.limit,
            target_bq=target_bq,
            target_gcs=target_gcs,
            local_output_file=args.output_file,
        )
        print(json.dumps(stats, indent=2))
    elif args.command == "verify":
        print(json.dumps(manager.emit_synthetic_test_log(), indent=2))
