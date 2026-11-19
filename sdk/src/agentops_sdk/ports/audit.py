# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Port contracts for compliance audit ledger sinks."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentops_sdk.domain import models


@runtime_checkable
class AuditSinkPort(Protocol):
    """Port contract for persisting immutable agent audit records."""

    def write_entry(self, entry: models.AuditLogEntry) -> bool:
        """Persists an AuditLogEntry and returns True on success."""
        ...
