"""Verifies Model Armor templates provisioned via central infrastructure IaC."""

from __future__ import annotations

import os

from resources import common


def _configured_templates() -> tuple[str, str]:
    """Resolves configured input and output Model Armor template IDs."""
    in_tpl = (
        os.getenv("MODEL_ARMOR_INPUT_TEMPLATE") or ""
    ).strip() or "agentops-input-guard"
    out_tpl = (
        os.getenv("MODEL_ARMOR_OUTPUT_TEMPLATE") or ""
    ).strip() or "agentops-output-guard"
    return in_tpl, out_tpl


def ensure(project: str, location: str) -> str:
    """Verifies that Model Armor templates exist in project and location.

    Args:
        project: GCP project ID hosting the Model Armor templates.
        location: GCP region where the Model Armor templates reside.

    Returns:
        Summary string listing verified template names.

    Raises:
        RuntimeError: If either configured template is missing or inaccessible.
    """
    base = (
        f"https://modelarmor.{location}.rep.googleapis.com/v1/"
        f"projects/{project}/locations/{location}/templates"
    )
    out = []
    for template_id in _configured_templates():
        code, data = common.call("GET", f"{base}/{template_id}")
        if code == 200:
            out.append(f"{template_id}: exists")
            continue
        raise RuntimeError(
            f"{template_id}: HTTP {code} {data.get('error', data)} "
            "(templates are provisioned via central infrastructure IaC)"
        )
    return "; ".join(out)
