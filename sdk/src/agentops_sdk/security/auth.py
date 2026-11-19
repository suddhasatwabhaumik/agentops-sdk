# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""JWT authentication, SSRF-safe JWKS validation, and PBKDF2 hashing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import datetime
import hashlib
import hmac
import logging
import os
import secrets
import socket  # Re-exported for test monkeypatching of socket.getaddrinfo.
import threading
from typing import Any

import jwt

from agentops_sdk.domain import exceptions
from agentops_sdk.security import _jwks

logger = logging.getLogger(__name__)

_PBKDF2_ITERATIONS = 600_000
_PBKDF2_PREFIX = "pbkdf2_sha256"
_MIN_HS256_SECRET_BYTES = 32
_DEV_ENVIRONMENTS = frozenset({"dev", "development", "local", "test"})
_EPHEMERAL_LOCK = threading.Lock()
_EPHEMERAL_DEV_SECRET: list[str] = []

validate_jwks_url = _jwks.validate_jwks_url


def _get_ephemeral_dev_secret() -> str:
    with _EPHEMERAL_LOCK:
        if not _EPHEMERAL_DEV_SECRET:
            _EPHEMERAL_DEV_SECRET.append(secrets.token_hex(32))
        return _EPHEMERAL_DEV_SECRET[0]


def _opt_str(
    val: str | None, env_key: str, *, arg_wins: bool = False
) -> str | None:
    if arg_wins and val is not None:
        return val.strip() or None
    return (val or os.getenv(env_key) or "").strip() or None


def _require_non_blank_if_given(value: str | None, field_name: str) -> None:
    if value is not None and not value.strip():
        raise exceptions.ConfigurationError(
            f"{field_name} must be a non-empty string when provided."
        )


class JWTAuthEngine:
    """Verifies and mints JWTs with pinned cryptographic algorithms."""

    def __init__(
        self,
        *,
        secret_key: str | None = None,
        jwks_url: str | None = None,
        audience: str | None = None,
        issuer: str | None = None,
        allowed_jwks_hosts: Sequence[str] | None = None,
        access_ttl_seconds: int = 900,
        refresh_ttl_seconds: int = 604_800,
    ) -> None:
        _require_non_blank_if_given(secret_key, "secret_key")
        _require_non_blank_if_given(jwks_url, "jwks_url")
        _require_non_blank_if_given(audience, "audience")
        _require_non_blank_if_given(issuer, "issuer")
        clean_sec = (secret_key or "").strip() or None
        resolved_jwks = (
            _opt_str(jwks_url, "AGENTOPS_IDENTITY__JWKS_URL")
            if jwks_url is not None or clean_sec is None
            else None
        )
        resolved_sec = clean_sec or _opt_str(None, "JWT_SECRET_KEY")
        self._audience = _opt_str(
            audience, "AGENTOPS_IDENTITY__CLIENT_ID", arg_wins=True
        )
        self._issuer = _opt_str(
            issuer, "AGENTOPS_IDENTITY__ISSUER", arg_wins=True
        )
        self._access_ttl = access_ttl_seconds
        self._refresh_ttl = refresh_ttl_seconds
        self._configure_keys(resolved_jwks, resolved_sec, allowed_jwks_hosts)

    def _configure_keys(
        self,
        jwks_url: str | None,
        secret_key: str | None,
        allowed: Sequence[str] | None,
    ) -> None:
        if jwks_url is not None:
            valid = validate_jwks_url(jwks_url, allowed_hosts=allowed)
            self._jwks_client: jwt.PyJWKClient | None = _jwks.SafeJWKClient(
                valid, allowed_hosts=allowed
            )
            self._algorithms: tuple[str, ...] = ("RS256",)
            self._secret_key: str | None = None
            return
        self._jwks_client = None
        self._algorithms = ("HS256",)
        env = os.getenv("ENVIRONMENT", "").strip().lower()
        is_dev = env in _DEV_ENVIRONMENTS
        if secret_key:
            if (
                not is_dev
                and len(secret_key.encode("utf-8")) < _MIN_HS256_SECRET_BYTES
            ):
                raise exceptions.ConfigurationError(
                    "HS256 secret_key must be >= 32 bytes in non-dev envs."
                )
            self._secret_key = secret_key
            return
        if not is_dev:
            raise exceptions.ConfigurationError(
                "Production and non-dev environments require JWKS URL or "
                "JWT_SECRET_KEY."
            )
        logger.warning("JWT_SECRET_KEY unset in %s; using ephemeral key.", env)
        self._secret_key = _get_ephemeral_dev_secret()

    def _mint(self, claims: Mapping[str, Any], kind: str, ttl: int) -> str:
        if self._algorithms != ("HS256",) or self._secret_key is None:
            raise exceptions.AuthenticationError("HS256 minting is disabled.")
        now = datetime.datetime.now(datetime.UTC)
        exp = now + datetime.timedelta(seconds=ttl)
        body: dict[str, Any] = {**claims, "iat": now, "exp": exp, "type": kind}
        if self._issuer and "iss" not in body:
            body["iss"] = self._issuer
        if self._audience and "aud" not in body:
            body["aud"] = self._audience
        return jwt.encode(body, self._secret_key, algorithm="HS256")

    def create_access_token(
        self, claims: Mapping[str, Any], *, ttl_seconds: int | None = None
    ) -> str:
        """Creates a signed short-lived access JWT."""
        return self._mint(claims, "access", ttl_seconds or self._access_ttl)

    def create_refresh_token(
        self, claims: Mapping[str, Any], *, ttl_seconds: int | None = None
    ) -> str:
        """Creates a signed refresh JWT."""
        return self._mint(claims, "refresh", ttl_seconds or self._refresh_ttl)

    def decode_token(
        self, token: str, *, expected_type: str = "access"
    ) -> dict[str, Any]:
        """Verifies JWT signature, expiry, type, issuer, and audience."""
        if not isinstance(expected_type, str) or not expected_type.strip():
            raise exceptions.ConfigurationError(
                "expected_type must be a non-empty string."
            )
        expected_clean = expected_type.strip()
        raw = token.strip()
        raw = raw[7:].strip() if raw.lower().startswith("bearer ") else raw
        if not raw:
            raise exceptions.AuthenticationError("JWT token must not be empty.")
        req = ["exp"] + (["aud"] if self._audience else [])
        if self._issuer:
            req.append("iss")
        try:
            key: Any = (
                self._jwks_client.get_signing_key_from_jwt(raw).key
                if self._jwks_client is not None
                else self._secret_key
            )
            decoded: dict[str, Any] = jwt.decode(
                raw,
                key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                options={"require": req, "verify_exp": True},
            )
        except jwt.PyJWTError as exc:
            raise exceptions.AuthenticationError("Invalid JWT token.") from exc
        if decoded.get("type") != expected_clean:
            raise exceptions.AuthenticationError(
                f"Invalid token type: expected {expected_clean!r}."
            )
        return decoded


def create_access_token(
    claims: Mapping[str, Any],
    *,
    secret_key: str | None = None,
    ttl_seconds: int = 900,
) -> str:
    """Creates a signed access JWT using JWTAuthEngine."""
    engine = JWTAuthEngine(secret_key=secret_key)
    return engine.create_access_token(claims, ttl_seconds=ttl_seconds)


def create_refresh_token(
    claims: Mapping[str, Any],
    *,
    secret_key: str | None = None,
    ttl_seconds: int = 604_800,
) -> str:
    """Creates a signed refresh JWT using JWTAuthEngine."""
    engine = JWTAuthEngine(secret_key=secret_key)
    return engine.create_refresh_token(claims, ttl_seconds=ttl_seconds)


def decode_jwt_token(
    token: str,
    *,
    expected_type: str = "access",
    secret_key: str | None = None,
    jwks_url: str | None = None,
) -> dict[str, Any]:
    """Decodes and validates a JWT using JWTAuthEngine."""
    engine = JWTAuthEngine(secret_key=secret_key, jwks_url=jwks_url)
    return engine.decode_token(token, expected_type=expected_type)


def hash_password(password: str) -> str:
    """Hashes a plaintext password with 600k-round PBKDF2-HMAC-SHA256."""
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string.")
    salt = os.urandom(16)
    pw_bytes = password.encode("utf-8")
    digest = hashlib.pbkdf2_hmac("sha256", pw_bytes, salt, _PBKDF2_ITERATIONS)
    return f"{_PBKDF2_PREFIX}${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded_hash: str) -> bool:
    """Verifies a password against a PBKDF2-HMAC-SHA256 hash."""
    if not password or not encoded_hash:
        return False
    parts = encoded_hash.split("$")
    if len(parts) != 4 or parts[0] != _PBKDF2_PREFIX:
        return False
    try:
        iters = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected = bytes.fromhex(parts[3])
    except ValueError:
        return False
    if not (_PBKDF2_ITERATIONS <= iters <= 10_000_000) or not salt:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iters
    )
    return hmac.compare_digest(digest, expected)


__all__ = [
    "JWTAuthEngine",
    "create_access_token",
    "create_refresh_token",
    "decode_jwt_token",
    "hash_password",
    "socket",
    "validate_jwks_url",
    "verify_password",
]
