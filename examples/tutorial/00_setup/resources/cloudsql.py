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


def _reset_user_password(project: str) -> str:
    password = secrets.token_urlsafe(18)
    _gcloud(
        "sql",
        "users",
        "create",
        USER,
        f"--instance={INSTANCE}",
        f"--project={project}",
        f"--password={password}",
        check=False,
    )
    _gcloud(
        "sql",
        "users",
        "set-password",
        USER,
        f"--instance={INSTANCE}",
        f"--project={project}",
        f"--password={password}",
    )
    return password


def ensure(project: str, location: str) -> str:
    """Provision Cloud SQL instance, database, and tutorial user."""
    _ensure_instance(project, location)
    _ensure_database(project)
    password = _reset_user_password(project)
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
        f"  Put in .env: DATABASE_URL=postgresql+psycopg2://{USER}:{password}"
        f"@127.0.0.1:5434/{DATABASE}\n"
        f"  Run the proxy:  cloud-sql-proxy --port 5434 {conn}"
    )
