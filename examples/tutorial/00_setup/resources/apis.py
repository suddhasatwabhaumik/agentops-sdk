"""Enables the two APIs the sandbox does not have yet (tutorials 10 and 12)."""

from __future__ import annotations

from resources import common

APIS = ("dlp.googleapis.com", "sqladmin.googleapis.com")


def ensure(project: str, location: str) -> str:
    out = []
    for api in APIS:
        code, data = common.call(
            "GET",
            f"https://serviceusage.googleapis.com/v1/projects/{project}/services/{api}",
        )
        if code == 200 and data.get("state") == "ENABLED":
            out.append(f"{api}: enabled")
            continue
        code, data = common.call(
            "POST",
            f"https://serviceusage.googleapis.com/v1/projects/{project}/services/{api}:enable",
            {},
        )
        if code != 200:
            raise RuntimeError(
                f"{api}: HTTP {code} {data.get('error', data)}"
            )  # needs serviceusage.services.enable
        out.append(f"{api}: enabling (operation started)")
    return "; ".join(out)
