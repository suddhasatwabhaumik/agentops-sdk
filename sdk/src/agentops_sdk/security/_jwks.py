# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""SSRF-safe JWKS URL validation and redirect-blocking PyJWKClient."""

from __future__ import annotations

from collections.abc import Sequence
import ipaddress
import json
import os
import socket
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import jwt
from typing_extensions import override

from agentops_sdk.domain import exceptions

_CGNAT_NETWORK = ipaddress.IPv4Network("100.64.0.0/10")


def _is_disallowed_ip(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if not addr.is_global:
        return True
    if (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_reserved
    ):
        return True
    if isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT_NETWORK:
        return True
    return isinstance(addr, ipaddress.IPv6Address) and (
        addr.ipv4_mapped is not None
        or addr.sixtofour is not None
        or addr.teredo is not None
    )


def _ensure_public_ip(raw_ip: str, host: str) -> None:
    try:
        ip_obj = ipaddress.ip_address(raw_ip.split("%", maxsplit=1)[0])
    except ValueError as exc:
        raise exceptions.AuthenticationError(
            f"Invalid JWKS IP resolved for {host!r}."
        ) from exc
    if _is_disallowed_ip(ip_obj):
        raise exceptions.AuthenticationError(
            f"Forbidden JWKS IP resolved for {host!r}."
        )


def _resolve_allowed_hosts(
    allowed: Sequence[str] | None,
) -> set[str] | None:
    env_hosts = os.getenv("AGENTOPS_IDENTITY__ALLOWED_JWKS_HOSTS", "").strip()
    raw_list = (
        allowed
        if allowed is not None
        else (env_hosts.split(",") if env_hosts else None)
    )
    if raw_list is None:
        return None
    return {
        item.strip().lower().rstrip(".") for item in raw_list if item.strip()
    }


def _check_host_and_dns(
    host: str, port: int, allowed: Sequence[str] | None
) -> None:
    clean_host = host.strip().lower().rstrip(".")
    if (
        not clean_host
        or clean_host == "localhost"
        or clean_host.endswith((".localhost", ".internal"))
    ):
        raise exceptions.AuthenticationError(f"Forbidden JWKS host: {host!r}.")
    allow_set = _resolve_allowed_hosts(allowed)
    if allow_set is not None and clean_host not in allow_set:
        raise exceptions.AuthenticationError(
            f"JWKS host {clean_host!r} not allowed."
        )
    try:
        infos = socket.getaddrinfo(clean_host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise exceptions.AuthenticationError(
            f"DNS lookup failed for {clean_host!r}."
        ) from exc
    if not infos:
        raise exceptions.AuthenticationError(
            f"No DNS records for {clean_host!r}."
        )
    for info in infos:
        _ensure_public_ip(str(info[4][0]), clean_host)


def validate_jwks_url(
    url: str, *, allowed_hosts: Sequence[str] | None = None
) -> str:
    """Validates a JWKS URL against SSRF, scheme, and host allowlist rules.

    Args:
        url: HTTPS URL pointing to the JWKS endpoint.
        allowed_hosts: Optional sequence of explicitly permitted hostnames.

    Returns:
        Normalized JWKS URL string.

    Raises:
        AuthenticationError: If scheme, host, DNS, or resolved IP is invalid.
    """
    cleaned = url.strip()
    parsed = urllib_parse.urlparse(cleaned)
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username
        or parsed.password
    ):
        raise exceptions.AuthenticationError(
            "JWKS URL must use https:// with a valid hostname and no userinfo."
        )
    _check_host_and_dns(host, parsed.port or 443, allowed_hosts)
    return cleaned


class _NoRedirectHandler(urllib_request.HTTPRedirectHandler):
    """Rejects HTTP 3xx redirects during JWKS fetches to prevent SSRF."""

    @override
    def redirect_request(
        self,
        req: urllib_request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib_request.Request | None:
        del req, fp, msg, headers
        raise exceptions.AuthenticationError(
            f"JWKS HTTP redirect ({code}) to {newurl!r} is forbidden."
        )


class SafeJWKClient(jwt.PyJWKClient):
    """PyJWKClient re-checking DNS/SSRF and blocking HTTP 3xx redirects."""

    def __init__(
        self,
        uri: str,
        *,
        allowed_hosts: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(uri, **kwargs)
        self._allowed_hosts = (
            tuple(allowed_hosts) if allowed_hosts is not None else None
        )

    @override
    def fetch_data(self) -> Any:
        """Validates DNS/IP before fetching JWKS without following 3xx."""
        validate_jwks_url(self.uri, allowed_hosts=self._allowed_hosts)
        # https:// scheme and public DNS/IP are enforced by validate_jwks_url.
        request = urllib_request.Request(  # noqa: S310
            url=self.uri, headers=self.headers
        )
        opener = urllib_request.build_opener(
            _NoRedirectHandler(),
            urllib_request.HTTPSHandler(context=self.ssl_context),
        )
        try:
            with opener.open(request, timeout=self.timeout) as response:
                jwk_set = json.load(response)
        except exceptions.AuthenticationError:
            raise
        except (
            urllib_error.URLError,
            TimeoutError,
            json.JSONDecodeError,
        ) as exc:
            if isinstance(exc, urllib_error.HTTPError):
                exc.close()
            raise jwt.PyJWKClientConnectionError(
                f'Fail to fetch data from the url, err: "{exc}"'
            ) from exc
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(jwk_set)
        return jwk_set
