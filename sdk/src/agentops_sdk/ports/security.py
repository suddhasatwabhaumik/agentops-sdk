# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Port contracts for content safety, DLP scrubbing, and secret storage."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentops_sdk.domain import models


@runtime_checkable
class SafetyScannerPort(Protocol):
    """Port contract for prompt-injection and content safety scanning."""

    def scan_input(self, text: str) -> models.SafetyScanResult:
        """Scans an inbound user prompt or untrusted tool context."""
        ...

    def scan_output(self, text: str) -> models.SafetyScanResult:
        """Scans an outbound model response and masks sensitive tokens."""
        ...


@runtime_checkable
class PrivacyScrubberPort(Protocol):
    """Port contract for PII inspection and de-identification."""

    def scrub(self, text: str) -> tuple[str, bool]:
        """Returns (scrubbed_text, was_modified)."""
        ...

    async def ascrub(self, text: str) -> tuple[str, bool]:
        """Asynchronously returns (scrubbed_text, was_modified)."""
        ...


@runtime_checkable
class SecretStorePort(Protocol):
    """Port contract for retrieving and storing application secrets."""

    def get_secret(self, secret_id: str, *, version: str = "latest") -> str:
        """Fetches secret plaintext by secret_id and version."""
        ...

    def put_secret(self, secret_id: str, payload: str) -> str:
        """Stores a new secret version and returns its resource identifier."""
        ...

    def delete_secret(self, secret_id: str) -> bool:
        """Deletes secret_id and returns True if removed."""
        ...
