# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Canonical domain input validation, log sanitization, and PII scrubbing."""

from __future__ import annotations

import re

_USER_ID_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?|\d{3}[\s.-])"
    r"\d{3}[\s.-]\d{4}(?!\w)"
)


def validate_user_id(user_id: str) -> str:
    """Validates user_id against filter-injection characters (CWE-20)."""
    if not isinstance(user_id, str) or not _USER_ID_RE.fullmatch(user_id):
        raise ValueError("Invalid user_id; expected ^[A-Za-z0-9._:@-]{1,128}$.")
    return user_id


def sanitize_log_field(value: object, *, max_length: int = 1024) -> str:
    """Strips CR/LF and control characters from log fields (CWE-117)."""
    if value is None:
        return ""
    cleaned = _CTRL_RE.sub(" ", str(value)).strip()
    if len(cleaned) > max_length:
        return cleaned[:max_length]
    return cleaned


def scrub_sensitive_text(text: str, max_length: int = 512) -> str:
    """Scrubs Bearer tokens, email addresses, and phone numbers (CWE-532)."""
    if not text:
        return ""
    out = _BEARER_RE.sub("Bearer [REDACTED_TOKEN]", text)
    out = _EMAIL_RE.sub("[REDACTED_EMAIL]", out)
    out = _PHONE_RE.sub("[REDACTED_PHONE]", out)
    return f"{out[:max_length]}...[truncated]" if len(out) > max_length else out
