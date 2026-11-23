# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Tutorial 07: JWT issuance and verification backed by Secret Manager."""

from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared import settings
from _shared import telemetry

cfg = settings.load("07_jwt_auth")
tel = telemetry.start(cfg)

from agentops_sdk import JWTAuthEngine
from agentops_sdk import SecretManagerStore
from agentops_sdk.domain.exceptions import AuthenticationError

USER = "tenant-4711"
STORE_TOKEN = os.getenv("TUTORIAL_STORE_TOKEN", "0") == "1"


def main() -> None:
    """Sign, verify, and reject tampered/expired JWTs using Secret Manager."""
    store = SecretManagerStore(project_id=cfg.project)
    signing_key = store.get_secret("adk-jwt-secret-key")
    engine = JWTAuthEngine(secret_key=signing_key, access_ttl_seconds=900)

    token = engine.create_access_token(
        claims={"sub": USER, "role": "tenant_user", "region": "EMEA"}
    )
    print(f"1) issued access token: {token[:28]}... ({len(token)} chars)")

    claims = engine.decode_token(token, expected_type="access")
    print(
        f"2) verified claims: sub={claims['sub']} "
        f"role={claims['role']} region={claims['region']}"
    )

    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    try:
        engine.decode_token(tampered, expected_type="access")
        sys.exit("Tampered token was accepted.")
    except AuthenticationError as exc:
        print(f"3) tampered rejected ({type(exc).__name__}): {exc}")

    expired = engine.create_access_token({"sub": USER}, ttl_seconds=-1)
    try:
        engine.decode_token(expired, expected_type="access")
        sys.exit("Expired token was accepted.")
    except AuthenticationError as exc:
        print(f"4) expired rejected ({type(exc).__name__}): {exc}")

    if STORE_TOKEN:
        secret_id = f"agentops-tutorial-token-{USER}"
        store.put_secret(secret_id, token)
        roundtrip = store.get_secret(secret_id)
        print(f"5) stored token matches: {roundtrip == token}")
    else:
        print("5) token storage skipped (set TUTORIAL_STORE_TOKEN=1 to store)")
    tel.flush()


if __name__ == "__main__":
    main()
