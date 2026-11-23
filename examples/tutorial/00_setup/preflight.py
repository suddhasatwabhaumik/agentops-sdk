# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Checks whether this machine and GCP identity can run each tutorial."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared import settings
import google.auth
from google.auth.transport.requests import Request


def _fetch_token() -> tuple[object, str, str, str]:
    cfg = settings.load("00_preflight")
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(Request())
    return creds, creds.token, cfg.project, cfg.location


def _http(
    token: str, method: str, url: str, body: dict | None = None
) -> tuple[int, dict]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"Expected https:// URL, got {url!r}")
    req = urllib.request.Request(  # noqa: S310 - https scheme validated above
        url,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 - https scheme validated
            req, timeout=20
        ) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except (json.JSONDecodeError, OSError):
            return exc.code, {}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, {"error": str(exc)}


def _granted(token: str, project: str, *perms: str) -> set[str]:
    _, data = _http(
        token,
        "POST",
        f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}"
        ":testIamPermissions",
        {"permissions": list(perms)},
    )
    return set(data.get("permissions", []))


def _enabled(token: str, project: str, api: str) -> bool:
    code, data = _http(
        token,
        "GET",
        f"https://serviceusage.googleapis.com/v1/projects/{project}"
        f"/services/{api}",
    )
    return code == 200 and data.get("state") == "ENABLED"


def _exists(token: str, url: str) -> bool:
    return _http(token, "GET", url)[0] == 200


def _bq_insert(token: str, project: str, dataset: str, table: str) -> list:
    _, data = _http(
        token,
        "POST",
        f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}"
        f"/datasets/{dataset}/tables/{table}:testIamPermissions",
        {"permissions": ["bigquery.tables.updateData"]},
    )
    return data.get("permissions", [])


def _port_open(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=2).close()
        return True
    except OSError:
        return False


def _stack_and_auth_checks(
    token: str, p: str, perms: set[str]
) -> dict[str, list[tuple[str, bool]]]:
    return {
        "stack": [
            ("OTLP localhost:4317", _port_open("localhost", 4317)),
            ("Tempo :3200", _port_open("localhost", 3200)),
            ("Prometheus :9090", _port_open("localhost", 9090)),
        ],
        "01 03 04 07 08 11 13": [
            (
                "Gemini / Vertex AI (aiplatform.endpoints.predict)",
                "aiplatform.endpoints.predict" in perms,
            ),
            (
                "Secret adk-jwt-secret-key readable (07, 08)",
                _exists(
                    token,
                    f"https://secretmanager.googleapis.com/v1/projects/{p}"
                    "/secrets/adk-jwt-secret-key",
                ),
            ),
        ],
    }


def _bigquery_checks(
    token: str, p: str, perms: set[str]
) -> dict[str, list[tuple[str, bool]]]:
    tutorial_table = f"{p}.agentops_tutorial.tenant_inventory"
    return {
        "05 06 sql guard + row-level security": [
            ("BigQuery jobs", "bigquery.jobs.create" in perms),
            (
                f"table {tutorial_table} (provision.py bigquery)",
                _exists(
                    token,
                    f"https://bigquery.googleapis.com/bigquery/v2/projects/{p}"
                    "/datasets/agentops_tutorial/tables/tenant_inventory",
                ),
            ),
        ],
        "02 case telemetry": [
            (
                "insert into agentops_observability.use_case_telemetry",
                bool(
                    _bq_insert(
                        token, p, "agentops_observability", "use_case_telemetry"
                    )
                ),
            )
        ],
        "14 audit ledger": [
            (
                "insert into agentops_governance.audit_ledger",
                bool(
                    _bq_insert(token, p, "agentops_governance", "audit_ledger")
                ),
            )
        ],
    }


def _safety_checks(
    token: str, p: str, loc: str, perms: set[str]
) -> dict[str, list[tuple[str, bool]]]:
    ma_base = (
        f"https://modelarmor.{loc}.rep.googleapis.com/v1/projects/{p}"
        f"/locations/{loc}/templates"
    )
    raw_in = os.getenv("MODEL_ARMOR_INPUT_TEMPLATE", "").strip()
    raw_out = os.getenv("MODEL_ARMOR_OUTPUT_TEMPLATE", "").strip()
    in_tpl = raw_in or "agentops-input-guard"
    out_tpl = raw_out or "agentops-output-guard"
    return {
        "09 model armor": [
            (
                "API modelarmor.googleapis.com",
                _enabled(token, p, "modelarmor.googleapis.com"),
            ),
            (
                f"template {in_tpl} in {loc}",
                _exists(token, f"{ma_base}/{in_tpl}"),
            ),
            (
                f"template {out_tpl} in {loc}",
                _exists(token, f"{ma_base}/{out_tpl}"),
            ),
        ],
        "10 dlp": [
            (
                "API dlp.googleapis.com",
                _enabled(token, p, "dlp.googleapis.com"),
            ),
            ("can enable APIs", "serviceusage.services.enable" in perms),
        ],
    }


def _memory_bank_location(bank: str, fallback: str) -> str:
    parts = [seg for seg in bank.split("/") if seg]
    if "locations" in parts:
        idx = parts.index("locations")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return os.getenv("MEMORY_BANK_LOCATION", fallback) or fallback


def _memory_and_fs_checks(
    token: str, p: str, loc: str, perms: set[str]
) -> dict[str, list[tuple[str, bool]]]:
    bank = os.getenv("MEMORY_BANK_NAME", "")
    bank_loc = _memory_bank_location(bank, loc)
    bank_url = f"https://{bank_loc}-aiplatform.googleapis.com/v1beta1/{bank}"
    return {
        "12 memory": [
            (
                "API sqladmin.googleapis.com",
                _enabled(token, p, "sqladmin.googleapis.com"),
            ),
            ("can create Cloud SQL", "cloudsql.instances.create" in perms),
            (
                "Cloud SQL proxy on 127.0.0.1:5434",
                _port_open("127.0.0.1", 5434),
            ),
            (
                "Agent Engine MEMORY_BANK_NAME exists",
                bool(bank) and _exists(token, bank_url),
            ),
            (
                "can create Agent Engine",
                "aiplatform.reasoningEngines.create" in perms,
            ),
        ],
        "15 feature store": [
            ("bigquery.datasets.create", "bigquery.datasets.create" in perms),
            (
                "aiplatform.featureOnlineStores.create",
                "aiplatform.featureOnlineStores.create" in perms,
            ),
        ],
    }


def _print_report(
    creds: object,
    project: str,
    location: str,
    checks: dict[str, list[tuple[str, bool]]],
) -> None:
    ident = getattr(creds, "service_account_email", None) or "user ADC"
    print(f"\nidentity: {ident} | project {project} | region {location}\n")
    blocked = 0
    for group, items in checks.items():
        ok = all(flag for _, flag in items)
        blocked += 0 if ok else 1
        print(f"{'READY  ' if ok else 'BLOCKED'}  {group}")
        for label, flag in items:
            print(f"           {'ok ' if flag else 'NO '} {label}")
    print(
        f"\n{blocked} group(s) blocked. "
        "See examples/tutorial/ACCESS.md for missing items."
    )


def main() -> None:
    """Run GCP and local stack readiness checks across all tutorials."""
    creds, token, p, loc = _fetch_token()
    perms = _granted(
        token,
        p,
        "bigquery.datasets.create",
        "bigquery.jobs.create",
        "modelarmor.templates.create",
        "cloudsql.instances.create",
        "aiplatform.reasoningEngines.create",
        "aiplatform.featureOnlineStores.create",
        "secretmanager.versions.access",
        "secretmanager.secrets.create",
        "serviceusage.services.enable",
        "aiplatform.endpoints.predict",
    )
    checks: dict[str, list[tuple[str, bool]]] = {}
    checks.update(_stack_and_auth_checks(token, p, perms))
    checks.update(_bigquery_checks(token, p, perms))
    checks.update(_safety_checks(token, p, loc, perms))
    checks.update(_memory_and_fs_checks(token, p, loc, perms))
    _print_report(creds, p, loc, checks)


if __name__ == "__main__":
    main()
