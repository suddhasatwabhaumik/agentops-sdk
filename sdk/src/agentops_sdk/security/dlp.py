# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Regional Google Cloud DLP scrubber, local regex scrubber, and sanitizer."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import contextlib
import re
import threading
from typing import Any

from google.cloud import dlp_v2
from opentelemetry import trace

from agentops_sdk import ports
from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.telemetry import provider

_DEFAULT_INFO_TYPES: tuple[str, ...] = (
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "PERSON_NAME",
    "CREDIT_CARD_NUMBER",
    "IBAN_CODE",
)
_MAX_CHUNK_BYTES = 400_000

_DLP_LOCK = threading.Lock()
_DLP_CLIENT_POOL: dict[str, dlp_v2.DlpServiceClient] = {}

_REGEX_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[EMAIL_ADDRESS]",
    ),
    (
        re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
        "[CREDIT_CARD_NUMBER]",
    ),
    (
        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
        "[IBAN_CODE]",
    ),
    (
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "[US_SOCIAL_SECURITY_NUMBER]",
    ),
    (
        re.compile(
            r"(?<!\w)(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})"
            r"[-.\s]\d{3}[-.\s]\d{4}(?!\w)"
        ),
        "[PHONE_NUMBER]",
    ),
    (
        re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
        ),
        "[IP_ADDRESS]",
    ),
)


def reset_dlp_clients() -> None:
    """Closes and clears pooled Cloud DLP clients."""
    with _DLP_LOCK:
        clients = list(_DLP_CLIENT_POOL.values())
        _DLP_CLIENT_POOL.clear()
    for client in clients:
        transport = getattr(client, "transport", None)
        close_fn = getattr(client, "close", None) or getattr(
            transport, "close", None
        )
        if callable(close_fn):
            with contextlib.suppress(Exception):
                close_fn()


def _get_dlp_client(location: str) -> dlp_v2.DlpServiceClient:
    """Returns a pooled regional or global Cloud DLP client."""
    clean_loc = location.strip()
    with _DLP_LOCK:
        cached = _DLP_CLIENT_POOL.get(clean_loc)
        if cached is None:
            endpoint = (
                "dlp.googleapis.com"
                if clean_loc == "global"
                else f"dlp.{clean_loc}.rep.googleapis.com"
            )
            cached = dlp_v2.DlpServiceClient(
                client_options={"api_endpoint": endpoint}
            )
            _DLP_CLIENT_POOL[clean_loc] = cached
    provider.register_reset_hook(reset_dlp_clients)
    return cached


def _chunk_utf8_text(
    text: str,
    max_bytes: int = _MAX_CHUNK_BYTES,
) -> list[str]:
    """Splits text into <= max_bytes UTF-8 chunks on safe boundaries."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return [text]
    chunks: list[str] = []
    start = 0
    total = len(encoded)
    while start < total:
        if total - start <= max_bytes:
            chunks.append(encoded[start:].decode("utf-8"))
            break
        end = start + max_bytes
        while end > start and 0x80 <= encoded[end] < 0xC0:
            end -= 1
        nl_idx = encoded.rfind(b"\n", start, end)
        sp_idx = encoded.rfind(b" ", start, end)
        split_at = (
            nl_idx + 1
            if nl_idx > start
            else (sp_idx + 1 if sp_idx > start else end)
        )
        chunks.append(encoded[start:split_at].decode("utf-8"))
        start = split_at
    return chunks


class GoogleDLPScrubber:
    """Regional Cloud DLP adapter implementing PrivacyScrubberPort."""

    def __init__(
        self,
        *,
        project_id: str | None = None,
        location: str | None = None,
        inspect_template_name: str | None = None,
        deidentify_template_name: str | None = None,
        info_types: Sequence[str] | None = None,
        client: Any | None = None,
    ) -> None:
        proj, loc = settings.resolve_gcp_project_and_location(
            project_id, location, require_project=True
        )
        self._project_id = proj or ""
        self._location = loc
        self._parent = f"projects/{self._project_id}/locations/{self._location}"
        self._inspect_template_name = inspect_template_name
        self._deidentify_template_name = deidentify_template_name
        self._info_types = tuple(info_types or _DEFAULT_INFO_TYPES)
        self._client = client

    def _build_request(self, chunk: str) -> dlp_v2.DeidentifyContentRequest:
        """Builds a regional DeidentifyContentRequest for one UTF-8 chunk."""
        inspect_cfg = (
            None
            if self._inspect_template_name
            else dlp_v2.InspectConfig(
                info_types=[dlp_v2.InfoType(name=t) for t in self._info_types]
            )
        )
        deid_cfg = (
            None
            if self._deidentify_template_name
            else dlp_v2.DeidentifyConfig(
                info_type_transformations=dlp_v2.InfoTypeTransformations(
                    transformations=[
                        dlp_v2.InfoTypeTransformations.InfoTypeTransformation(
                            primitive_transformation=(
                                dlp_v2.PrimitiveTransformation(
                                    replace_with_info_type_config=(
                                        dlp_v2.ReplaceWithInfoTypeConfig()
                                    )
                                )
                            )
                        )
                    ]
                )
            )
        )
        return dlp_v2.DeidentifyContentRequest(
            parent=self._parent,
            inspect_config=inspect_cfg,
            deidentify_config=deid_cfg,
            inspect_template_name=self._inspect_template_name or "",
            deidentify_template_name=self._deidentify_template_name or "",
            item=dlp_v2.ContentItem(value=chunk),
        )

    def scrub(self, text: str) -> tuple[str, bool]:
        """De-identifies PII across chunked UTF-8 text via Cloud DLP."""
        if not text:
            return "", False
        client = self._client or _get_dlp_client(self._location)
        out_parts: list[str] = []
        try:
            for chunk in _chunk_utf8_text(text):
                resp = client.deidentify_content(
                    request=self._build_request(chunk), timeout=15.0
                )
                out_parts.append(str(resp.item.value))
        except Exception as exc:
            raise exceptions.PrivacyScrubError(
                "Google Cloud DLP de-identification failed."
            ) from exc
        scrubbed = "".join(out_parts)
        return scrubbed, scrubbed != text

    async def ascrub(self, text: str) -> tuple[str, bool]:
        """Asynchronously scrubs PII via Cloud DLP."""
        return await asyncio.to_thread(self.scrub, text)


class RegexPrivacyScrubber:
    """Linear-time offline regex scrubber for local testing."""

    def scrub(self, text: str) -> tuple[str, bool]:
        """Scrubs common PII tokens using bounded regular expressions."""
        if not text:
            return "", False
        scrubbed = text
        for pattern, replacement in _REGEX_PATTERNS:
            scrubbed = pattern.sub(replacement, scrubbed)
        return scrubbed, scrubbed != text

    async def ascrub(self, text: str) -> tuple[str, bool]:
        """Asynchronously scrubs PII using bounded regular expressions."""
        return self.scrub(text)


class OutputDLPSanitizer:
    """Sanitizes outbound text via PrivacyScrubberPort and tags OTel spans."""

    def __init__(
        self,
        scrubber: ports.security.PrivacyScrubberPort | None = None,
        *,
        project_id: str | None = None,
        location: str | None = None,
    ) -> None:
        self._scrubber: ports.security.PrivacyScrubberPort = (
            scrubber
            if scrubber is not None
            else GoogleDLPScrubber(project_id=project_id, location=location)
        )

    def _record_span(self, modified: bool) -> None:
        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute("agentops.privacy.pii_scrubbed", modified)

    def scrub(self, text: str) -> str:
        """Scrubs PII synchronously and sets agentops.privacy.pii_scrubbed."""
        scrubbed, modified = self._scrubber.scrub(text)
        self._record_span(modified)
        return scrubbed

    async def scrub_text(self, text: str) -> str:
        """Scrubs PII asynchronously and sets agentops.privacy.pii_scrubbed."""
        scrubbed, modified = await self._scrubber.ascrub(text)
        self._record_span(modified)
        return scrubbed
