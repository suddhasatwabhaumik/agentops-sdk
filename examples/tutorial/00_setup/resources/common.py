"""Tiny REST helper shared by provisioning modules (token from ADC)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import google.auth
from google.auth.transport.requests import Request

_token: str | None = None


def token() -> str:
    global _token
    if _token is None:
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        creds.refresh(Request())
        _token = creds.token
    return _token


def call(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"Expected https:// URL, got {url!r}")
    req = urllib.request.Request(  # noqa: S310 - https scheme validated above
        url,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 - https scheme validated
            req, timeout=60
        ) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except Exception:  # noqa: BLE001
            return exc.code, {"error": exc.reason}
