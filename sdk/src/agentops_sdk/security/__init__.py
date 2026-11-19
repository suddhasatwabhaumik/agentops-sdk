# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Security, DLP privacy scrubbing, JWT auth, and secret management adapters."""

from __future__ import annotations

from agentops_sdk.security import auth
from agentops_sdk.security import dlp
from agentops_sdk.security import guard
from agentops_sdk.security import identity
from agentops_sdk.security import model_armor
from agentops_sdk.security import secrets

CloudModelArmorScanner = model_armor.CloudModelArmorScanner
GoogleDLPScrubber = dlp.GoogleDLPScrubber
InMemorySecretStore = secrets.InMemorySecretStore
JWTAuthEngine = auth.JWTAuthEngine
LocalRuleSafetyScanner = guard.LocalRuleSafetyScanner
OutputDLPSanitizer = dlp.OutputDLPSanitizer
RegexPrivacyScrubber = dlp.RegexPrivacyScrubber
SecretManagerStore = secrets.SecretManagerStore
SecurityGuard = guard.SecurityGuard
check_input = guard.check_input
check_output = guard.check_output
create_access_token = auth.create_access_token
create_refresh_token = auth.create_refresh_token
decode_jwt_token = auth.decode_jwt_token
fetch_secret = identity.fetch_secret
get_current_identity = identity.get_current_identity
get_user_identity_token = identity.get_user_identity_token
hash_password = auth.hash_password
inject_secret = identity.inject_secret
pass_user_identity = identity.pass_user_identity
validate_jwks_url = auth.validate_jwks_url
verify_password = auth.verify_password

__all__ = [
    "CloudModelArmorScanner",
    "GoogleDLPScrubber",
    "InMemorySecretStore",
    "JWTAuthEngine",
    "LocalRuleSafetyScanner",
    "OutputDLPSanitizer",
    "RegexPrivacyScrubber",
    "SecretManagerStore",
    "SecurityGuard",
    "auth",
    "check_input",
    "check_output",
    "create_access_token",
    "create_refresh_token",
    "decode_jwt_token",
    "dlp",
    "fetch_secret",
    "get_current_identity",
    "get_user_identity_token",
    "guard",
    "hash_password",
    "identity",
    "inject_secret",
    "model_armor",
    "pass_user_identity",
    "secrets",
    "validate_jwks_url",
    "verify_password",
]
