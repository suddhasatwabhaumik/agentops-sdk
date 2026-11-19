# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Loads examples/tutorial/.env and exposes common settings for tutorials."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
import uuid

from dotenv import load_dotenv

TUTORIAL_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = TUTORIAL_ROOT / ".env"


@dataclass(frozen=True)
class Settings:
    """Common configuration values read from .env for each tutorial run."""

    tutorial: str
    project: str
    location: str
    collector_url: str
    service_name: str
    environment: str
    run_id: str


def require(name: str) -> str:
    """Return a required environment variable or exit with guidance."""
    value = (os.getenv(name) or "").strip()
    if not value or value.startswith("<your-"):
        sys.exit(
            f"Missing or placeholder {name} in {ENV_FILE} (see .env.example)."
        )
    return value


def _drop_ipv6_from_no_proxy() -> None:
    """Strip literal IPv6 entries in NO_PROXY that break httpx URL parsing."""
    for name in ("NO_PROXY", "no_proxy"):
        raw = os.getenv(name)
        if raw and "::" in raw:
            cleaned = ",".join(e for e in raw.split(",") if "::" not in e)
            os.environ[name] = cleaned


def load(tutorial: str) -> Settings:
    """Load .env into os.environ, reset SDK caches, and return Settings."""
    if not ENV_FILE.exists():
        sys.exit(
            f"Missing {ENV_FILE}. Copy .env.example to .env and fill it in."
        )
    load_dotenv(ENV_FILE, override=False)
    _drop_ipv6_from_no_proxy()

    from agentops_sdk.config.settings import reset_settings_cache

    reset_settings_cache()
    project = require("GOOGLE_CLOUD_PROJECT")
    os.environ.setdefault("AGENTOPS_PROJECT_ID", project)

    settings = Settings(
        tutorial=tutorial,
        project=project,
        location=os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west4"),
        collector_url=os.getenv("OTEL_COLLECTOR_URL", "http://localhost:4317"),
        service_name=os.getenv("OTEL_SERVICE_NAME", "agentops-tutorial"),
        environment=os.getenv("ENVIRONMENT", "dev"),
        run_id=f"{tutorial}-{uuid.uuid4().hex[:8]}",
    )
    print(
        f"== {tutorial} | project={settings.project} "
        f"location={settings.location} collector={settings.collector_url} "
        f"run_id={settings.run_id}"
    )
    return settings
