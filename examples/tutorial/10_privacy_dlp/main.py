# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Tutorial 10: redact PII from agent output with Cloud DLP."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared import gemini
from _shared import settings
from _shared import telemetry

cfg = settings.load("10_privacy_dlp")
tel = telemetry.start(cfg)

from opentelemetry import trace

from agentops_sdk import GoogleDLPScrubber
from agentops_sdk import OutputDLPSanitizer
from agentops_sdk import trace_agent_flow
from agentops_sdk import trace_llm_call


@trace_llm_call(
    kpi_id="dealer-communication",
    pl_impact_line="aftersales-opex",
)
def draft_email():
    """Draft a dealer message containing realistic contact PII."""
    return gemini.generate(
        "Write a 3-line e-mail to dealer contact Hans Mueller "
        "(hans.mueller@example.com, phone +49 171 5550123) "
        "confirming that oil filter PART-1001 ships tomorrow.",
        system="Plain text, no subject line.",
    )


@trace_agent_flow(
    flow_name="dlp_demo",
    kpi_id="dealer-communication",
    pl_impact_line="aftersales-opex",
)
async def main() -> None:
    """Scrub PII with explicit GoogleDLPScrubber (no silent regex fallback)."""
    scrubber = GoogleDLPScrubber(project_id=cfg.project)
    sanitizer = OutputDLPSanitizer(scrubber=scrubber)

    raw = draft_email().text
    print(f"\nraw draft:\n{raw}\n")
    clean = await sanitizer.scrub_text(raw)
    print(f"after Cloud DLP:\n{clean}\n")

    span = trace.get_current_span()
    attrs = getattr(span, "attributes", {}) or {}
    scrubbed_flag = attrs.get("agentops.privacy.pii_scrubbed")
    print(f"span agentops.privacy.pii_scrubbed={scrubbed_flag}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        tel.flush()
