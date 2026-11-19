# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Port contracts for telemetry and per-case telemetry exporters."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentops_sdk.domain import models


@runtime_checkable
class CaseTelemetryExporterPort(Protocol):
    """Port contract for exporting structured per-case telemetry records."""

    def export(self, record: models.UseCaseTelemetry) -> bool:
        """Exports a single UseCaseTelemetry record; returns True on success."""
        ...
