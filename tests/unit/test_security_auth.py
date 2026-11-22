# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for agentops_sdk.security.auth and identity propagation."""

from __future__ import annotations

import datetime
from email.message import Message
import io
import secrets
import socket
from typing import Any
from urllib import request as urllib_request
from urllib import response as urllib_response

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest

from agentops_sdk.domain import context as sdk_context
from agentops_sdk.domain import exceptions
from agentops_sdk.security import InMemorySecretStore
from agentops_sdk.security import JWTAuthEngine
from agentops_sdk.security import _jwks
from agentops_sdk.security import auth
from agentops_sdk.security import create_access_token
from agentops_sdk.security import create_refresh_token
from agentops_sdk.security import decode_jwt_token
from agentops_sdk.security import fetch_secret
from agentops_sdk.security import get_current_identity
from agentops_sdk.security import get_user_identity_token
from agentops_sdk.security import hash_password
from agentops_sdk.security import inject_secret
from agentops_sdk.security import pass_user_identity
from agentops_sdk.security import validate_jwks_url
from agentops_sdk.security import verify_password


def test_jwt_mint_decode_and_fail_closed_envs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = secrets.token_hex(32)
    eng = JWTAuthEngine(
        secret_key=key, audience="agentops-api", issuer="agentops-idp"
    )
    tok = eng.create_access_token({"sub": "alice", "tenant_id": "t1"})
    claims = eng.decode_token(f"Bearer {tok}")
    assert claims["sub"] == "alice" and claims["aud"] == "agentops-api"

    ref_tok = eng.create_refresh_token({"sub": "alice"})
    with pytest.raises(exceptions.AuthenticationError, match="token type"):
        eng.decode_token(ref_tok, expected_type="access")
    assert eng.decode_token(ref_tok, expected_type="refresh")["sub"] == "alice"

    for env_name in ("production", "staging"):
        monkeypatch.setenv("ENVIRONMENT", env_name)
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.delenv("AGENTOPS_IDENTITY__JWKS_URL", raising=False)
        with pytest.raises(exceptions.ConfigurationError, match="Production"):
            JWTAuthEngine()
        with pytest.raises(exceptions.ConfigurationError, match=">= 32 bytes"):
            JWTAuthEngine(secret_key="too-short")

    tok2 = create_access_token({"sub": "bob"}, secret_key=key)
    ref2 = create_refresh_token({"sub": "bob"}, secret_key=key)
    assert decode_jwt_token(tok2, secret_key=key)["sub"] == "bob"
    assert (
        decode_jwt_token(ref2, expected_type="refresh", secret_key=key)["sub"]
        == "bob"
    )


def test_jwt_blank_config_and_expected_type_rejected() -> None:
    key = secrets.token_hex(32)
    with pytest.raises(exceptions.ConfigurationError, match="audience"):
        JWTAuthEngine(secret_key=key, audience="   ")
    with pytest.raises(exceptions.ConfigurationError, match="issuer"):
        JWTAuthEngine(secret_key=key, issuer="")
    eng = JWTAuthEngine(secret_key=key)
    tok = eng.create_access_token({"sub": "alice"})
    with pytest.raises(exceptions.ConfigurationError, match="expected_type"):
        eng.decode_token(tok, expected_type="   ")


def test_validate_jwks_url_ssrf_and_redirect_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://idp.example.com/.well-known/jwks.json"
    monkeypatch.setattr(
        auth.socket,
        "getaddrinfo",
        lambda *_a, **_kw: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 443))],
    )
    assert validate_jwks_url(url, allowed_hosts=["idp.example.com"]) == url
    with pytest.raises(exceptions.AuthenticationError, match="not allowed"):
        validate_jwks_url(url, allowed_hosts=["other.example.com"])

    for bad_ip in ("::ffff:127.0.0.1", "100.64.0.1"):
        monkeypatch.setattr(
            auth.socket,
            "getaddrinfo",
            lambda *_a, ip=bad_ip, **_kw: [
                (socket.AF_INET6, 0, 0, "", (ip, 443, 0, 0))
            ],
        )
        with pytest.raises(exceptions.AuthenticationError, match="Forbidden"):
            validate_jwks_url(url)

    def _dns_err(*_a: Any, **_kw: Any) -> Any:
        raise socket.gaierror("fail")

    monkeypatch.setattr(auth.socket, "getaddrinfo", _dns_err)
    with pytest.raises(exceptions.AuthenticationError, match="DNS"):
        validate_jwks_url(url)


def test_safe_jwks_client_blocks_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://idp.example.com/.well-known/jwks.json"
    monkeypatch.setattr(
        auth.socket,
        "getaddrinfo",
        lambda *_a, **_kw: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 443))],
    )
    hdrs = Message()
    hdrs["Location"] = "https://169.254.169.254/latest"
    resp = urllib_response.addinfourl(io.BytesIO(b""), hdrs, url, code=302)
    resp.msg = "Found"
    monkeypatch.setattr(
        urllib_request.HTTPSHandler, "https_open", lambda *_a, **_kw: resp
    )
    client = _jwks.SafeJWKClient(url)
    with pytest.raises(exceptions.AuthenticationError, match="redirect"):
        client.fetch_data()


def test_pbkdf2_password_hashing_and_verification() -> None:
    with pytest.raises(ValueError):
        hash_password("")
    encoded = hash_password("CorrectHorseBatteryStaple!")
    assert encoded.startswith("pbkdf2_sha256$600000$")
    assert verify_password("CorrectHorseBatteryStaple!", encoded) is True
    assert verify_password("wrong-password", encoded) is False
    assert verify_password("", encoded) is False
    assert verify_password("x", "pbkdf2_sha256$100$aa$bb") is False
    assert verify_password("x", "bad-hash") is False


async def test_secret_injection_positional_and_keyword() -> None:
    store = InMemorySecretStore({"api-secret": "s3cr3t"})
    assert fetch_secret("api-secret", store=store) == "s3cr3t"
    with pytest.raises(ValueError):
        inject_secret("api-secret", param_name="not-valid")

    @inject_secret("api-secret", param_name="db_pass", store=store)
    async def read_kw(*, db_pass: str = "") -> str:
        return db_pass

    @inject_secret("api-secret", param_name="db_pass", store=store)
    def read_pos(db_pass: str, /, tag: str) -> tuple[str, str]:
        return db_pass, tag

    assert await read_kw(db_pass="spoofed") == "s3cr3t"
    assert read_pos("spoofed", "ok") == ("s3cr3t", "ok")


def test_identity_propagation_and_outer_scope_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = secrets.token_hex(32)
    eng = JWTAuthEngine(secret_key=key)
    tok = eng.create_access_token({"sub": "u-42", "tenant_id": "t-9"})

    @pass_user_identity(jwt_engine=eng)
    def protected(auth_token: str) -> tuple[str, str | None]:
        del auth_token
        sc = get_current_identity()
        return sc.user_id, get_user_identity_token()

    assert protected(auth_token=f"Bearer {tok}") == ("u-42", tok)
    with pytest.raises(exceptions.AuthenticationError):
        protected(auth_token="")

    @pass_user_identity()
    def unauthenticated_inner(auth_token: str | None = None) -> tuple[str, Any]:
        del auth_token
        sc = get_current_identity()
        return sc.user_id, get_user_identity_token()

    with sdk_context.bind_execution_scope(
        user_id="outer-user", identity_token="outer-tok"
    ):
        assert unauthenticated_inner(auth_token="") == ("", None)
        assert get_current_identity().user_id == "outer-user"
        assert get_user_identity_token() == "outer-tok"

    monkeypatch.setenv("DEV_RBAC_USER", "dev-alice")
    monkeypatch.setenv("ENVIRONMENT", "development")

    @pass_user_identity()
    def dev_only() -> str:
        return get_current_identity().user_id

    assert dev_only() == "dev-alice"
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert dev_only() == ""


def test_rsa_pem_public_key_and_rs256_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    priv = rsa.generate_private_key(65537, key_size=2048)
    pub_pem = (
        priv.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    assert _jwks.parse_rsa_public_pem(pub_pem.replace("\n", "\\n")) is not None

    ec_pem = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    for bad_pem in ("not-a-pem", "-----BEGIN PUBLIC KEY-----\nbad", ec_pem):
        with pytest.raises(exceptions.ConfigurationError, match="Invalid RSA"):
            _jwks.parse_rsa_public_pem(bad_pem)

    monkeypatch.setattr(
        auth.socket,
        "getaddrinfo",
        lambda *_a, **_kw: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 443))],
    )
    for kw in (
        {"public_key": pub_pem},
        {"public_key": pub_pem, "audience": "agentops-api"},
        {"public_key": pub_pem, "issuer": "https://idp.example.com/v2.0"},
        {"jwks_url": "https://idp.example.com/jwks.json", "audience": "a"},
    ):
        with pytest.raises(exceptions.ConfigurationError, match="RS256"):
            JWTAuthEngine(**kw)

    eng = JWTAuthEngine(
        public_key=pub_pem,
        audience="agentops-api",
        issuer="https://idp.example.com/v2.0",
    )
    now = datetime.datetime.now(datetime.UTC)
    exp = now + datetime.timedelta(minutes=5)
    typeless_tok = jwt.encode(
        {
            "sub": "alice",
            "aud": "agentops-api",
            "iss": "https://idp.example.com/v2.0",
            "exp": exp,
        },
        priv,
        algorithm="RS256",
    )
    assert eng.decode_token(typeless_tok)["sub"] == "alice"

    refresh_tok = jwt.encode(
        {
            "sub": "alice",
            "aud": "agentops-api",
            "iss": "https://idp.example.com/v2.0",
            "exp": exp,
            "type": "refresh",
        },
        priv,
        algorithm="RS256",
    )
    with pytest.raises(exceptions.AuthenticationError, match="token type"):
        eng.decode_token(refresh_tok, expected_type="access")


def test_entra_aliases_single_tenant_issuer_and_hs256_strict_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        _jwks.build_entra_issuer("example-tenant-01")
        == "https://login.microsoftonline.com/example-tenant-01/v2.0"
    )
    for bad_tid in ("", "common", "Organizations", "consumers", "../bad"):
        with pytest.raises(exceptions.ConfigurationError, match="Entra"):
            _jwks.build_entra_issuer(bad_tid)

    priv = rsa.generate_private_key(65537, key_size=2048)
    pub_pem = (
        priv.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    monkeypatch.setenv("ENTRA_CLIENT_ID", "agentops-api")
    monkeypatch.setenv("ENTRA_TENANT_ID", "example-tenant-01")
    monkeypatch.setenv("MCP_JWT_PUBLIC_KEY", pub_pem)
    eng = JWTAuthEngine()
    exp = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5)
    tok = jwt.encode(
        {
            "sub": "entra-user",
            "aud": "agentops-api",
            "iss": "https://login.microsoftonline.com/example-tenant-01/v2.0",
            "exp": exp,
        },
        priv,
        algorithm="RS256",
    )
    assert eng.decode_token(tok)["sub"] == "entra-user"

    hs_key = secrets.token_hex(32)
    hs_eng = JWTAuthEngine(
        secret_key=hs_key, audience="agentops-api", issuer="agentops-idp"
    )
    hs_typeless = jwt.encode(
        {
            "sub": "bob",
            "aud": "agentops-api",
            "iss": "agentops-idp",
            "exp": exp,
        },
        hs_key,
        algorithm="HS256",
    )
    with pytest.raises(exceptions.AuthenticationError, match="token type"):
        hs_eng.decode_token(hs_typeless)
