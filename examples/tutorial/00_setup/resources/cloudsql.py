# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Cloud SQL for PostgreSQL instance provisioning for tutorial 12."""

from __future__ import annotations

import secrets
import subprocess

INSTANCE = "agentops-tutorial"
DATABASE = "agentops"
USER = "agentops"


def _gcloud(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["gcloud", *args, "--quiet"]
    print("  $", " ".join(cmd))
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def _ensure_instance(project: str, location: str) -> None:
    probe = _gcloud(
        "sql",
        "instances",
        "describe",
        INSTANCE,
        f"--project={project}",
        check=False,
    )
    if probe.returncode != 0:
        _gcloud(
            "sql",
            "instances",
            "create",
            INSTANCE,
            f"--project={project}",
            f"--region={location}",
            "--database-version=POSTGRES_16",
            "--tier=db-f1-micro",
            "--edition=ENTERPRISE",
            "--storage-size=10GB",
            "--assign-ip",
        )


def _ensure_database(project: str) -> None:
    probe = _gcloud(
        "sql",
        "databases",
        "describe",
        DATABASE,
        f"--instance={INSTANCE}",
        f"--project={project}",
        check=False,
    )
    if probe.returncode != 0:
        _gcloud(
            "sql",
            "databases",
            "create",
            DATABASE,
            f"--instance={INSTANCE}",
            f"--project={project}",
        )


def _ensure_user(project: str) -> tuple[str, str]:
    probe = _gcloud(
        "sql",
        "users",
        "describe",
        USER,
        f"--instance={INSTANCE}",
        f"--project={project}",
        check=False,
    )
    if probe.returncode == 0:
        return (
            "<existing password, unchanged>",
            "  Note: existing user preserved; password was not rotated.",
        )
    password = secrets.token_urlsafe(18)
    _gcloud(
        "sql",
        "users",
        "create",
        USER,
        f"--instance={INSTANCE}",
        f"--project={project}",
        f"--password={password}",
    )
    return (
        password,
        "  Note: created user with generated password; save it now.",
    )


def ensure(project: str, location: str) -> str:
    """Provision Cloud SQL instance, database, and tutorial user."""
    _ensure_instance(project, location)
    _ensure_database(project)
    password, note = _ensure_user(project)
    conn = _gcloud(
        "sql",
        "instances",
        "describe",
        INSTANCE,
        f"--project={project}",
        "--format=value(connectionName)",
    ).stdout.strip()
    return (
        f"instance {conn} ready.\n"
        f"{note}\n"
        f"  Put in .env: DATABASE_URL=postgresql+psycopg2://{USER}:{password}"
        f"@127.0.0.1:5434/{DATABASE}\n"
        f"  Run the proxy:  cloud-sql-proxy --port 5434 {conn}"
    )
