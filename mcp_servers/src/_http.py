# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""HTTP URL validation, bind config, and ASGI header middleware for MCP."""

from __future__ import annotations

from collections.abc import Mapping
import ipaddress
import os
from typing import Any
from urllib import parse as urllib_parse

from agentops_sdk.domain import exceptions
from mcp_servers.src import auth_guard

DEFAULT_CONNECTOR_API_URL = "http://127.0.0.1:8081"
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8080


def is_loopback_host(host: str) -> bool:
    """Checks whether a parsed URL hostname targets loopback."""
    cleaned = host.strip().lower().rstrip(".")
    if cleaned == "localhost" or cleaned.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(cleaned).is_loopback
    except ValueError:
        return False


def allow_insecure_connector_http() -> bool:
    """Returns True when non-loopback plain HTTP override is explicitly set."""
    raw = os.getenv("ALLOW_INSECURE_CONNECTOR_HTTP", "false")
    return raw.strip().lower() in {"true", "1", "yes"}


def get_connector_api_url() -> str:
    """Validates and normalizes CONNECTOR_API_URL from the environment."""
    raw = os.getenv("CONNECTOR_API_URL", DEFAULT_CONNECTOR_API_URL).strip()
    parsed = urllib_parse.urlparse(raw)
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    if (
        scheme not in {"http", "https"}
        or not parsed.netloc
        or not host
        or parsed.username
        or parsed.password
    ):
        raise exceptions.ConfigurationError(
            f"Invalid CONNECTOR_API_URL: {raw!r}"
        )
    if (
        scheme == "http"
        and not is_loopback_host(host)
        and not allow_insecure_connector_http()
    ):
        raise exceptions.ConfigurationError(
            "Non-loopback CONNECTOR_API_URL requires https:// "
            "(set ALLOW_INSECURE_CONNECTOR_HTTP=true to override)."
        )
    return raw.rstrip("/")


def resolve_bind_config(
    host: str | None = None,
    port: int | None = None,
) -> tuple[str, int]:
    """Resolves and validates host and port bindings for FastMCP."""
    res_host = (
        host if host is not None else os.getenv("MCP_HOST", DEFAULT_MCP_HOST)
    ).strip()
    if not res_host or any(c.isspace() for c in res_host) or "/" in res_host:
        raise exceptions.ConfigurationError(f"Invalid MCP_HOST: {res_host!r}")
    if port is not None:
        res_port = port
    else:
        raw_port = os.getenv("PORT", "").strip()
        try:
            res_port = int(raw_port) if raw_port else DEFAULT_MCP_PORT
        except ValueError as exc:
            raise exceptions.ConfigurationError(
                f"Invalid PORT: {raw_port!r}"
            ) from exc
    if not 1 <= res_port <= 65535:
        raise exceptions.ConfigurationError(f"Invalid PORT range: {res_port}")
    return res_host, res_port


class HeaderBindingMiddleware:
    """ASGI middleware binding HTTP request headers via bind_request_headers."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self, scope: Mapping[str, Any], receive: Any, send: Any
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        raw_headers = scope.get("headers") or ()
        headers = {
            (
                k.decode("latin-1")
                if isinstance(k, bytes | bytearray)
                else str(k)
            ): (
                v.decode("latin-1")
                if isinstance(v, bytes | bytearray)
                else str(v)
            )
            for k, v in raw_headers
        }
        with auth_guard.bind_request_headers(headers):
            await self.app(scope, receive, send)


def attach_header_middleware(asgi_app: Any) -> Any:
    """Attaches HeaderBindingMiddleware to a Starlette or ASGI app."""
    add_mw = getattr(asgi_app, "add_middleware", None)
    if callable(add_mw):
        add_mw(HeaderBindingMiddleware)
        return asgi_app
    return HeaderBindingMiddleware(asgi_app)


def build_http_asgi_app(server: Any, transport: str) -> Any | None:
    """Builds the HTTP ASGI app for SSE or streamable-HTTP transports."""
    attr = (
        "sse_app"
        if transport == "sse"
        else "streamable_http_app"
        if transport == "streamable-http"
        else None
    )
    if attr is None:
        return None
    factory = getattr(server, attr, None)
    if not callable(factory):
        return None
    return attach_header_middleware(factory())
