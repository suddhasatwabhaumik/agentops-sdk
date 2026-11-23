# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Tutorial 08: identity propagation and Secret Manager injection."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared import settings
from _shared import telemetry

cfg = settings.load("08_identity_and_secrets")
tel = telemetry.start(cfg)

from agentops_sdk import JWTAuthEngine
from agentops_sdk import SecretManagerStore
from agentops_sdk import get_user_identity_token
from agentops_sdk import inject_secret
from agentops_sdk import pass_user_identity
from agentops_sdk import trace_agent_flow
from agentops_sdk import trace_agent_span
from agentops_sdk.domain.exceptions import SecretAccessError

SUPPLIER_CREDENTIAL_ID = "agentops-tutorial-supplier-api-key"
STORE_CREDENTIAL = os.getenv("TUTORIAL_STORE_SECRET", "0") == "1"
SECRET_STORE = SecretManagerStore(project_id=cfg.project)


@trace_agent_span(agent_name="whoami_tool")
def whoami_tool(jwt_engine: JWTAuthEngine) -> str:
    """Read caller identity from ExecutionScope without passing token args."""
    token = get_user_identity_token()
    if not token:
        raise RuntimeError("No caller token bound in ExecutionScope.")
    claims = jwt_engine.decode_token(token, expected_type="access")
    return f"{claims['sub']} ({claims['role']}, {claims['region']})"


@inject_secret(
    SUPPLIER_CREDENTIAL_ID, param_name="secret_payload", store=SECRET_STORE
)
@trace_agent_span(agent_name="supplier_tool")
def supplier_tool(part_number: str, secret_payload: str = "") -> str:
    """Inject cached Secret Manager payload as `secret_payload`."""
    data = json.loads(secret_payload) if secret_payload else {}
    api_key = str(data.get("api_key", ""))
    return (
        f"called supplier API for {part_number} (key suffix ...{api_key[-4:]})"
        if api_key
        else "no key available"
    )


@pass_user_identity()
@trace_agent_flow(
    flow_name="identity_demo",
    kpi_id="tenant-parts-availability",
    pl_impact_line="aftersales-opex",
)
def handle_request(
    part_number: str, auth_token: str, jwt_engine: JWTAuthEngine
) -> None:
    """Propagate caller auth token and invoke secret-injected tool."""
    del auth_token
    print(f"\n1) identity seen by nested tool: {whoami_tool(jwt_engine)}")
    try:
        print(f"2) {supplier_tool(part_number)}")
    except SecretAccessError as exc:
        print(
            f"2) secret '{SUPPLIER_CREDENTIAL_ID}' not readable yet "
            f"({type(exc).__name__}). Run with TUTORIAL_STORE_SECRET=1."
        )


def main() -> None:
    """Issue gateway token, optionally seed secret, and run flow."""
    signing_key = SECRET_STORE.get_secret("adk-jwt-secret-key")
    jwt_engine = JWTAuthEngine(secret_key=signing_key)
    caller_token = jwt_engine.create_access_token(
        {"sub": "tenant-4711", "role": "tenant_user", "region": "EMEA"}
    )
    if STORE_CREDENTIAL:
        payload = json.dumps({"api_key": f"demo-{secrets.token_hex(8)}"})
        SECRET_STORE.put_secret(SUPPLIER_CREDENTIAL_ID, payload)
        print(f"stored new secret version in {SUPPLIER_CREDENTIAL_ID}")
    handle_request("PART-1001", auth_token=caller_token, jwt_engine=jwt_engine)
    tel.flush()


if __name__ == "__main__":
    main()
