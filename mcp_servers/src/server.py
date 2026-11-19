# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Model Context Protocol (MCP) server gateway for enterprise queries.

Exposes MCP tools for BigQuery/Snowflake, Databricks SQL Warehouse, and Vertex
AI Feature Online Store with JWT auth, SQL AST checks, and RLS enforcement.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import importlib
import json
import logging
import os
from typing import Any

import httpx

from agentops_sdk.domain import exceptions
from mcp_servers.src import _http
from mcp_servers.src import auth_guard

logger = logging.getLogger(__name__)

DEFAULT_CONNECTOR_API_URL = _http.DEFAULT_CONNECTOR_API_URL
DEFAULT_MCP_HOST = _http.DEFAULT_MCP_HOST
DEFAULT_MCP_PORT = _http.DEFAULT_MCP_PORT
_REQUEST_TIMEOUT_SECONDS = 15.0

authorize_query = auth_guard.authorize_query
bind_request_headers = auth_guard.bind_request_headers
validate_and_authorize_sql = auth_guard.validate_and_authorize_sql
_HeaderBindingMiddleware = _http.HeaderBindingMiddleware
attach_header_middleware = _http.attach_header_middleware
_get_connector_api_url = _http.get_connector_api_url
_resolve_bind_config = _http.resolve_bind_config


def _post_connector_query(
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
) -> str:
    url = f"{_get_connector_api_url()}/v1/query"
    resp = httpx.post(
        url,
        json=dict(payload),
        headers=dict(headers),
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.text


def _execute_sql_tool(
    query_template: str,
    parameters: Mapping[str, Any] | None,
    *,
    dialect: str,
    error_prefix: str,
) -> str:
    ok, sql_or_err, headers = validate_and_authorize_sql(
        query_template, dialect=dialect
    )
    if not ok:
        return f"{error_prefix}: {sql_or_err}"
    payload = {
        "query_template": sql_or_err,
        "parameters": dict(parameters or {}),
    }
    try:
        return _post_connector_query(payload, headers)
    except httpx.HTTPError as exc:
        logger.warning(
            "%s connector error: %s", error_prefix, type(exc).__name__
        )
        return f"{error_prefix}: Connector service request failed."
    except exceptions.ConfigurationError as exc:
        logger.warning(
            "%s configuration error: %s", error_prefix, type(exc).__name__
        )
        return f"{error_prefix}: {exc}"


def query_database_tool(
    query_template: str,
    parameters: Mapping[str, Any] | None = None,
) -> str:
    """Executes a read-only analytical SQL query against BigQuery/Snowflake."""
    return _execute_sql_tool(
        query_template,
        parameters,
        dialect="bigquery",
        error_prefix="Tool Execution Failure",
    )


def query_databricks_tool(
    query_template: str,
    parameters: Mapping[str, Any] | None = None,
) -> str:
    """Executes a read-only SQL query against Databricks SQL Warehouse."""
    return _execute_sql_tool(
        query_template,
        parameters,
        dialect="databricks",
        error_prefix="Databricks Tool Failure",
    )


def query_feature_store_tool(
    entity_type: str,
    entity_ids: Sequence[str],
    features: Sequence[str],
) -> str:
    """Queries Vertex AI Feature Store for online feature values by entity."""
    try:
        claims, headers = auth_guard.authenticate_caller()
        tenant_id = auth_guard.authorize_feature_store_tenant(
            entity_type, entity_ids, claims
        )
        tpl = {
            "entity_type": entity_type.strip(),
            "entity_ids": list(entity_ids),
            "features": list(features),
        }
        payload: dict[str, Any] = {
            "query_template": json.dumps(tpl),
            "parameters": {"tenant_id": tenant_id} if tenant_id else {},
        }
        return _post_connector_query(payload, headers)
    except exceptions.AuthenticationError as exc:
        logger.warning("Feature Store auth failed: %s", type(exc).__name__)
        return f"Feature Store Tool Failure: Authentication Failure: {exc}"
    except httpx.HTTPError as exc:
        logger.warning("Feature Store query failed: %s", type(exc).__name__)
        return "Feature Store Tool Failure: Connector service request failed."
    except (exceptions.ConfigurationError, ValueError) as exc:
        logger.warning("Feature Store query failed: %s", type(exc).__name__)
        return f"Feature Store Tool Failure: {exc}"


def create_mcp_server(
    host: str | None = None,
    port: int | None = None,
) -> Any:
    """Creates a FastMCP server instance with registered gateway tools."""
    resolved_host, resolved_port = _resolve_bind_config(host, port)
    try:
        fastmcp_mod = importlib.import_module("mcp.server.fastmcp")
    except ImportError as exc:
        raise ImportError(
            "Install mcp package (`pip install 'mcp[cli]>=1.0.0'`) to run MCP."
        ) from exc
    server = fastmcp_mod.FastMCP(
        "GCP Connector MCP Gateway", host=resolved_host, port=resolved_port
    )
    server.tool()(query_database_tool)
    server.tool()(query_databricks_tool)
    server.tool()(query_feature_store_tool)
    return server


def main(argv: Sequence[str] | None = None) -> None:
    """Runs the MCP server gateway process."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="AgentOps MCP Server Gateway")
    parser.add_argument(
        "--transport",
        default=os.getenv("MCP_TRANSPORT", "stdio"),
        choices=["stdio", "sse", "streamable-http"],
        help="MCP transport protocol (default: stdio)",
    )
    parser.add_argument("--host", default=None, help="Bind host override")
    parser.add_argument("--port", type=int, default=None, help="Bind port")
    args, _ = parser.parse_known_args(list(argv) if argv is not None else None)
    resolved_host, resolved_port = _resolve_bind_config(args.host, args.port)
    mcp_server = create_mcp_server(host=resolved_host, port=resolved_port)
    asgi_app = _http.build_http_asgi_app(mcp_server, args.transport)
    if asgi_app is not None:
        try:
            uvicorn_mod = importlib.import_module("uvicorn")
        except ImportError:
            mcp_server.run(transport=args.transport)
            return
        uvicorn_mod.run(asgi_app, host=resolved_host, port=resolved_port)
        return
    mcp_server.run(transport=args.transport)


if __name__ == "__main__":
    main()
