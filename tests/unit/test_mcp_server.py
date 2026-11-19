# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for mcp_servers.src.auth_guard and mcp_servers.src.server."""

from __future__ import annotations

import inspect
import json
import secrets
import sys
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from agentops_sdk.domain import context as sdk_context
from agentops_sdk.domain import exceptions
from agentops_sdk.security import auth
from mcp_servers.src import auth_guard
from mcp_servers.src import server


def test_auth_guard_token_precedence_and_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    assert auth_guard.is_auth_required() is True
    monkeypatch.setenv("MCP_REQUIRE_AUTH", "false")
    assert auth_guard.is_auth_required() is False
    monkeypatch.setenv("MCP_ALLOWED_TABLES", "orders, prod.items")
    assert auth_guard.get_allowed_tables() == ["orders", "prod.items"]

    with auth_guard.bind_request_headers({"Authorization": "Bearer h-tok"}):
        assert auth_guard.resolve_bearer_token() == "h-tok"
        assert auth_guard.resolve_bearer_token(token="arg-tok") == "arg-tok"
    with sdk_context.bind_execution_scope(identity_token="ctx-tok"):
        assert auth_guard.resolve_bearer_token() == "ctx-tok"

    monkeypatch.setenv("MCP_AUTH_TOKEN", "server-secret-not-caller")
    assert auth_guard.resolve_bearer_token() == ""
    monkeypatch.setenv("MCP_REQUIRE_AUTH", "true")
    assert "Authentication Failure" in server.query_database_tool("SELECT 1")

    monkeypatch.setenv("JWT_SECRET_KEY", secrets.token_hex(32))
    eng1 = auth_guard.get_jwt_engine()
    assert auth_guard.get_jwt_engine() is eng1
    monkeypatch.setenv("JWT_SECRET_KEY", secrets.token_hex(32))
    assert auth_guard.get_jwt_engine() is not eng1
    auth_guard.clear_jwt_engine_cache()
    assert auth_guard.get_jwt_engine() is not eng1

    fake_fm = SimpleNamespace(
        get_context=lambda: SimpleNamespace(
            request_context=SimpleNamespace(
                request=SimpleNamespace(headers={"authorization": "Bearer fm"})
            )
        )
    )
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_fm)
    assert auth_guard.resolve_bearer_token() == "fm"


def test_authorize_query_auth_before_ast_and_rls() -> None:
    key = secrets.token_hex(32)
    eng = auth.JWTAuthEngine(secret_key=key)
    tok = eng.create_access_token({"sub": "alice", "tenant_id": "t-1"})

    with pytest.raises(exceptions.AuthenticationError, match="Missing bearer"):
        auth_guard.authorize_query(
            "SELECT * FROM forbidden_table", allowed_tables=["orders"]
        )

    ok, sql, hdrs = auth_guard.validate_and_authorize_sql(
        "SELECT id FROM orders",
        token=tok,
        allowed_tables=["orders"],
        jwt_engine=eng,
    )
    assert ok is True and "tenant_id = 't-1'" in sql
    assert hdrs == {"Authorization": f"Bearer {tok}"}
    bad_ok, bad_msg, _ = auth_guard.validate_and_authorize_sql(
        "DROP TABLE orders", token=tok, jwt_engine=eng
    )
    assert bad_ok is False and "SQL AST Security Violation" in bad_msg


def test_authorize_feature_store_tenant_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_ALLOWED_ENTITY_TYPES", "inventory")
    assert (
        auth_guard.authorize_feature_store_tenant(
            "inventory", ["t-1:a"], {"tenant_id": "t-1"}
        )
        == "t-1"
    )
    for etype, ids, claims in (
        ("secret_type", ["t-1:a"], {"tenant_id": "t-1"}),
        ("inventory", ["a"], {"sub": "x"}),
        ("inventory", ["t-2:a"], {"tenant_id": "t-1"}),
        (
            "inventory",
            ["t-1:a"],
            {"tenant_id": "t-1", "allowed_entity_types": ["x"]},
        ),
        (
            "inventory",
            ["t-1:b"],
            {"tenant_id": "t-1", "allowed_entity_ids": ["t-1:a"]},
        ),
    ):
        with pytest.raises(exceptions.AuthenticationError):
            auth_guard.authorize_feature_store_tenant(etype, ids, claims)
    assert (
        auth_guard.authorize_feature_store_tenant(
            "inventory", ["t-2:a"], {"role": "admin"}
        )
        is None
    )


def test_authorize_feature_store_malformed_claims() -> None:
    for claims in (
        {"tenant_id": "t-1", "allowed_entity_types": 123},
        {"tenant_id": "t-1", "allowed_entity_ids": {"a": 1}},
        {"tenant_id": "t-1", "allowed_entity_types": [123]},
        {"tenant_id": "t-1", "allowed_entity_ids": b"abc"},
    ):
        with pytest.raises(exceptions.AuthenticationError):
            auth_guard.authorize_feature_store_tenant(
                "inventory", ["t-1:a"], claims
            )


def test_mcp_tools_signature_and_missing_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for fn in (
        server.query_database_tool,
        server.query_databricks_tool,
        server.query_feature_store_tool,
    ):
        assert "auth_token" not in inspect.signature(fn).parameters
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("MCP_REQUIRE_AUTH", "true")
    assert "Authentication Failure" in server.query_database_tool("SELECT 1")
    assert "Authentication Failure" in server.query_feature_store_tool(
        "inventory", ["sku-1"], ["qty"]
    )


def test_mcp_tools_execute_with_bound_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = secrets.token_hex(32)
    monkeypatch.setenv("JWT_SECRET_KEY", key)
    tok = auth.create_access_token(
        {"sub": "alice", "tenant_id": "tenant-acme"}, secret_key=key
    )
    posted: list[dict[str, Any]] = []

    def _fake_post(url: str, **kw: Any) -> Any:
        posted.append({"url": url, **kw})
        return SimpleNamespace(text="[]", raise_for_status=lambda: None)

    monkeypatch.setattr(server.httpx, "post", _fake_post)
    hdr = {"Authorization": f"Bearer {tok}"}
    with server.bind_request_headers(hdr):
        r_db = server.query_database_tool("SELECT id FROM orders")
        r_dbx = server.query_databricks_tool("SELECT id FROM delta_tbl")
        r_fs = server.query_feature_store_tool(
            "inventory", ["tenant-acme:sku-1"], ["stock_level"]
        )
    assert (r_db, r_dbx, r_fs) == ("[]", "[]", "[]")
    assert all(p["headers"] == hdr for p in posted)
    assert "tenant_id = 'tenant-acme'" in posted[0]["json"]["query_template"]
    assert "tenant_id = 'tenant-acme'" in posted[1]["json"]["query_template"]
    fs_tpl = json.loads(posted[2]["json"]["query_template"])
    assert fs_tpl["entity_ids"] == ["tenant-acme:sku-1"]
    assert posted[2]["json"]["parameters"] == {"tenant_id": "tenant-acme"}


def test_mcp_server_config_errors_and_sanitized_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for bad_url in ("ftp://bad", "http://connector.internal:8081"):
        monkeypatch.setenv("CONNECTOR_API_URL", bad_url)
        with pytest.raises(exceptions.ConfigurationError):
            server._get_connector_api_url()
    monkeypatch.setenv("CONNECTOR_API_URL", "http://connector.internal:8081")
    monkeypatch.setenv("ALLOW_INSECURE_CONNECTOR_HTTP", "true")
    assert server._get_connector_api_url() == "http://connector.internal:8081"
    monkeypatch.delenv("CONNECTOR_API_URL", raising=False)
    monkeypatch.setenv("PORT", "")
    assert server._resolve_bind_config() == ("127.0.0.1", 8080)
    with pytest.raises(exceptions.ConfigurationError):
        server._resolve_bind_config(host="bad host", port=8080)
    with pytest.raises(exceptions.ConfigurationError):
        server._resolve_bind_config(host="127.0.0.1", port=70000)

    monkeypatch.setattr(
        server.httpx,
        "post",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            httpx.ConnectError("secret-internal-host:8081 down")
        ),
    )
    monkeypatch.setenv("MCP_REQUIRE_AUTH", "false")
    for res in (
        server.query_database_tool("SELECT 1"),
        server.query_databricks_tool("SELECT 1"),
        server.query_feature_store_tool("inventory", ["sku-1"], ["qty"]),
    ):
        assert "Connector service request failed." in res
        assert "secret-internal-host" not in res


async def test_header_binding_middleware_and_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def _asgi_app(scope: dict[str, Any], _recv: Any, _send: Any) -> None:
        seen.append(f"{scope['type']}:{auth_guard.resolve_bearer_token()}")

    wrapped = server.attach_header_middleware(_asgi_app)
    starlette_mws: list[Any] = []
    starlette_like = SimpleNamespace(add_middleware=starlette_mws.append)
    assert server.attach_header_middleware(starlette_like) is starlette_like
    assert starlette_mws == [server._HeaderBindingMiddleware]
    await wrapped(
        {"type": "http", "headers": [(b"authorization", b"Bearer asgi-tok")]},
        None,
        None,
    )
    await wrapped({"type": "lifespan"}, None, None)
    assert seen == ["http:asgi-tok", "lifespan:"]
    assert auth_guard.resolve_bearer_token() == ""

    registered: list[Any] = []
    run_calls: list[str] = []
    fake_fm = SimpleNamespace(
        FastMCP=lambda *_a, **_kw: SimpleNamespace(
            tool=lambda: registered.append,
            run=lambda *, transport: run_calls.append(transport),
        )
    )
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_fm)
    server.main(
        ["--transport", "stdio", "--host", "127.0.0.1", "--port", "9090"]
    )
    assert len(registered) == 3 and run_calls == ["stdio"]
