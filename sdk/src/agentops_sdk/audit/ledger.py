# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Compliance AuditLedger facade delegating strictly to AuditSinkPort."""

from __future__ import annotations

import asyncio

from agentops_sdk.audit import sinks
from agentops_sdk.domain import models
from agentops_sdk.domain import validation
from agentops_sdk.ports import audit as audit_ports


def _sanitize_audit_entry(entry: models.AuditLogEntry) -> models.AuditLogEntry:
    """Scrubs emails, phone numbers, and Bearer tokens from error_message."""
    if not entry.error_message:
        return entry
    scrubbed = validation.scrub_sensitive_text(
        entry.error_message, max_length=2048
    )
    if scrubbed == entry.error_message:
        return entry
    return entry.model_copy(update={"error_message": scrubbed})


class AuditLedger:
    """Async and sync compliance audit facade backed by an AuditSinkPort."""

    def __init__(self, sink: audit_ports.AuditSinkPort | None = None) -> None:
        """Initializes AuditLedger with an optional AuditSinkPort."""
        self._sink: audit_ports.AuditSinkPort = (
            sink if sink is not None else sinks.CloudLoggingAuditAdapter()
        )

    @property
    def sink(self) -> audit_ports.AuditSinkPort:
        """The active AuditSinkPort backing this ledger."""
        return self._sink

    def log_entry_sync(self, entry: models.AuditLogEntry) -> bool:
        """Scrubs error_message and writes an AuditLogEntry synchronously."""
        sanitized = _sanitize_audit_entry(entry)
        return self._sink.write_entry(sanitized)

    async def log_entry(self, entry: models.AuditLogEntry) -> bool:
        """Scrubs error_message and persists entry via asyncio.to_thread."""
        sanitized = _sanitize_audit_entry(entry)
        return await asyncio.to_thread(self._sink.write_entry, sanitized)
