# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Adversarial CWE regressions: SQL, code, auth, and rate limits."""

from __future__ import annotations

import asyncio
import base64
from email.message import Message
import hashlib
import hmac
import io
import json
import secrets
import socket
from types import SimpleNamespace
from typing import Any
from urllib import request as urllib_request
from urllib import response as urllib_response

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.api_core import exceptions as gcp_exceptions
import pytest

from agentops_sdk.domain import context
from agentops_sdk.domain import exceptions
from agentops_sdk.evaluation import validate_metric_code
from agentops_sdk.memory import validate_user_id
from agentops_sdk.resilience import SyncRateLimiter
from agentops_sdk.resilience import rate_limit
from agentops_sdk.security import InMemorySecretStore
from agentops_sdk.security import JWTAuthEngine
from agentops_sdk.security import SecretManagerStore
from agentops_sdk.security import _jwks
from agentops_sdk.security import auth
from agentops_sdk.security import hash_password
from agentops_sdk.security import inject_secret
from agentops_sdk.security import pass_user_identity
from agentops_sdk.security import validate_jwks_url
from agentops_sdk.security import verify_password
from agentops_sdk.sql import apply_row_level_policy
from agentops_sdk.sql import validate_sql_query
from agentops_sdk.sql import validate_sql_query_decorator
from mcp_servers.src import auth_guard


def test_cwe_sqli_ast_bypasses_and_allowlists() -> None:
    bad_sqls = (
        ("SELECT * FROM orders; DROP TABLE orders;", ["orders"]),
        ("SELECT * FROM orders JOIN users ON orders.id = users.id", ["orders"]),
        ("SELECT * FROM (orders)", ["users"]),
        ("SELECT * FROM orders JOIN (users)", ["orders"]),
        ("SELECT * FROM read_csv('/etc/passwd')", ["orders"]),
        ("SELECT * FROM system.access.audit", None),
        ("SELECT * INTO new_tbl FROM orders", ["orders"]),
        ("SELECT * FROM orders", ["*"]),
        (
            "WITH orders AS (SELECT * FROM secret_tbl) SELECT * FROM orders",
            ["orders"],
        ),
        ("SELECT * FROM evil_proj.public.orders", ["public.orders"]),
        ("SELECT * FROM orders", []),
    )
    for sql, allow in bad_sqls:
        assert validate_sql_query(sql, allowed_tables=allow).is_safe is False


def test_cwe_sqli_decorator_defaults_varargs_and_empty_params() -> None:
    with pytest.raises(
        exceptions.SQLGovernanceError, match="must not be empty"
    ):
        validate_sql_query_decorator(allowed_tables=["orders"], sql_params=())

    @validate_sql_query_decorator(allowed_tables=["orders"])
    def with_default(sql: str = "DROP TABLE orders") -> str:
        return sql

    with pytest.raises(exceptions.SQLGovernanceError):
        with_default()

    @validate_sql_query_decorator(allowed_tables=["orders"])
    def with_varargs(*queries: str) -> tuple[str, ...]:
        return queries

    with pytest.raises(exceptions.SQLGovernanceError):
        with_varargs("SELECT 1 FROM orders", "SELECT * FROM users")


def test_cwe_rls_paren_unqualified_and_missing_identity() -> None:
    sql = (
        "WITH x AS (SELECT id FROM orders) "
        "SELECT * FROM x JOIN invoices AS i ON x.id = i.id "
        "WHERE i.status = 'open' OR i.status = 'due' "
        "AND i.id IN (SELECT ref_id FROM payments)"
    )
    rewritten = apply_row_level_policy(sql, {"tenant_id": "*"})
    assert rewritten.count("tenant_id = '*'") == 3
    assert "(i.status = 'open' OR i.status = 'due'" in rewritten
    paren_sql = apply_row_level_policy(
        "SELECT * FROM (orders) JOIN (users)", {"tenant_id": "t1"}
    )
    assert "orders.tenant_id = 't1'" in paren_sql
    assert "users.tenant_id = 't1'" in paren_sql
    unqual = apply_row_level_policy(
        "SELECT * FROM orders",
        {"tenant_id": "t1"},
        target_tables=["prod.orders"],
    )
    assert "orders.tenant_id = 't1'" in unqual
    for bad in ({}, {"sub": "no-tenant"}):
        with pytest.raises(exceptions.SQLGovernanceError):
            apply_row_level_policy("SELECT id FROM orders", bad)


def test_cwe_94_metric_code_ast_bypasses_blocked() -> None:
    bypasses = (
        "def evaluate(r): return ().__class__.__base__.__subclasses__()",
        "import builtins\ndef evaluate(r): return 1",
        "import pathlib\ndef evaluate(r): return 1",
        "def evaluate(r): return getattr(__builtins__, 'eval')('1')",
        "from os import system\ndef evaluate(r): return 1",
    )
    for bad in bypasses:
        with pytest.raises(ValueError):
            validate_metric_code(bad, required_fn="evaluate")


async def test_cwe_secret_antispoofing_and_permission_eviction() -> None:
    mem_store = InMemorySecretStore({"db-key": "real-secret"})

    @inject_secret("db-key", param_name="secret_value", store=mem_store)
    def get_db(secret_value: str = "") -> str:
        return secret_value

    assert get_db(secret_value="attacker-override") == "real-secret"
    revoked = [False]

    def _access(*, request: dict[str, str], **_kw: Any) -> SimpleNamespace:
        del request
        if revoked[0]:
            raise gcp_exceptions.PermissionDenied("revoked")
        return SimpleNamespace(payload=SimpleNamespace(data=b"v1-data"))

    sm = SecretManagerStore(
        project_id="test-proj",
        client=SimpleNamespace(access_secret_version=_access),
    )
    assert sm.get_secret("tok") == "v1-data"
    revoked[0] = True
    with pytest.raises(exceptions.SecretAccessError):
        sm.get_secret("tok", version="1")
    assert sm._cache.get(("test-proj", "tok", "latest")) is None


async def test_cwe_identity_user_id_and_async_isolation() -> None:
    for bad_uid in ("../admin", "u1;DROP", "u1 type=x", ""):
        with pytest.raises(ValueError):
            validate_user_id(bad_uid)
    eng = JWTAuthEngine(secret_key=secrets.token_hex(32))
    t1 = eng.create_access_token({"sub": "alice"})
    t2 = eng.create_access_token({"sub": "bob"})

    @pass_user_identity(jwt_engine=eng)
    async def whoami(auth_token: str) -> str:
        del auth_token
        await asyncio.sleep(0.01)
        return context.current_execution_scope().user_id

    assert await asyncio.gather(
        whoami(auth_token=t1), whoami(auth_token=t2)
    ) == ["alice", "bob"]


def _forge_hs256_jwt(payload: dict[str, Any], secret_bytes: bytes) -> str:
    def _b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    hdr = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    pay = _b64(json.dumps(payload).encode("utf-8"))
    msg = f"{hdr}.{pay}".encode("ascii")
    sig = _b64(hmac.new(secret_bytes, msg, hashlib.sha256).digest())
    return f"{hdr}.{pay}.{sig}"


def test_cwe_jwt_alg_confusion_claims_and_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sec = secrets.token_hex(32)
    eng = JWTAuthEngine(
        secret_key=sec, audience="agentops-app", issuer="agentops-iss"
    )
    hdr = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=")
    pay = base64.urlsafe_b64encode(b'{"sub":"root","exp":9999999999}').rstrip(
        b"="
    )
    with pytest.raises(exceptions.AuthenticationError):
        eng.decode_token(f"{hdr.decode()}.{pay.decode()}.")
    exp_claims = {
        "sub": "alice",
        "exp": 1,
        "type": "access",
        "aud": "agentops-app",
        "iss": "agentops-iss",
    }
    with pytest.raises(exceptions.AuthenticationError):
        eng.decode_token(_forge_hs256_jwt(exp_claims, sec.encode("utf-8")))
    pub = rsa.generate_private_key(65537, key_size=2048).public_key()
    pub_pem = pub.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    monkeypatch.setattr(
        auth.socket,
        "getaddrinfo",
        lambda *_a, **_kw: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 443))],
    )
    rs_eng = JWTAuthEngine(
        jwks_url="https://idp.example.com/jwks.json",
        audience="agentops-app",
        issuer="agentops-iss",
    )
    rs_eng._jwks_client = SimpleNamespace(  # type: ignore[assignment]
        get_signing_key_from_jwt=lambda _t: SimpleNamespace(key=pub)
    )
    with pytest.raises(exceptions.AuthenticationError):
        rs_eng.decode_token(
            _forge_hs256_jwt({"sub": "evil", "exp": 9999999999}, pub_pem)
        )


def test_cwe_pbkdf2_and_jwks_ssrf_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hashed = hash_password("StrongPassword!99")
    assert hashed.startswith("pbkdf2_sha256$600000$")
    assert verify_password("StrongPassword!99", hashed) is True
    for bad_url in (
        "http://idp.example.com/jwks.json",
        "https://user:pass@idp.example.com/jwks.json",
        "https://localhost/jwks.json",
        "https://metadata.google.internal/jwks.json",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.1/jwks.json",
    ):
        with pytest.raises(exceptions.AuthenticationError):
            validate_jwks_url(bad_url)
    for bad_ip in ("169.254.169.254", "::ffff:127.0.0.1", "100.64.0.1"):
        monkeypatch.setattr(
            auth.socket,
            "getaddrinfo",
            lambda *_a, ip=bad_ip, **_kw: [
                (socket.AF_INET, 0, 0, "", (ip, 443))
            ],
        )
        with pytest.raises(exceptions.AuthenticationError, match="Forbidden"):
            validate_jwks_url("https://rebind.example.com/jwks.json")


def test_cwe_jwks_http_redirect_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://idp.example.com/jwks.json"
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
    with pytest.raises(exceptions.AuthenticationError, match="redirect"):
        _jwks.SafeJWKClient(url).fetch_data()


def test_cwe_mcp_auth_and_rate_limiter_tenant_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_REQUIRE_AUTH", raising=False)
    assert auth_guard.is_auth_required() is True
    with pytest.raises(exceptions.AuthenticationError):
        auth_guard.authorize_feature_store_tenant(
            "orders", ["tenant-b:1"], {"tenant_id": "tenant-a"}
        )

    lim = SyncRateLimiter()

    @rate_limit(rule_id="r1", rate=1, burst=1, limiter=lim)
    def op(tenant_id: str) -> str:
        return tenant_id

    assert op(tenant_id="t-evil") == "t-evil"
    with pytest.raises(exceptions.RateLimitExceededError):
        op(tenant_id="t-other")
    with context.bind_execution_scope(claims={"tenant_id": "t-verified"}):
        assert op(tenant_id="ignored") == "ignored"
