# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Google Cloud Model Armor scanner adapter."""

from __future__ import annotations

import contextlib
import os
import threading
from typing import Any

from google.cloud import modelarmor_v1

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import models
from agentops_sdk.security import dlp
from agentops_sdk.telemetry import provider

_CLIENT_LOCK = threading.Lock()
_CLIENT_POOL: dict[str, modelarmor_v1.ModelArmorClient] = {}
_THREAT_CHECKS = (
    ("pi_and_jailbreak_filter_result", "prompt_injection"),
    ("rai_filter_result", "rai_violation"),
    ("malicious_uri_filter_result", "malicious_uri"),
    ("csam_filter_filter_result", "csam_violation"),
    ("virus_scan_filter_result", "virus_detected"),
)
_SECURITY_LOC_ENVS = (
    "MODEL_ARMOR_LOCATION",
    "MODEL_ARMOR_REGION",
    "SECURITY_LOCATION",
    "SECURITY_REGION",
)
_SECURITY_PROJ_ENVS = (
    "MODEL_ARMOR_PROJECT_ID",
    "SECURITY_PROJECT_ID",
)


class _CompatInstrumentationHook:
    """Compatibility hook delegating cache resets to telemetry.provider."""

    def cache_clear(self) -> None:
        """Clears CachedMeterTracker in telemetry.provider."""
        provider.get_meter_tracker().clear()


_get_instruments = _CompatInstrumentationHook()


def reset_model_armor_clients() -> None:
    """Closes and clears pooled Model Armor clients."""
    with _CLIENT_LOCK:
        clients = list(_CLIENT_POOL.values())
        _CLIENT_POOL.clear()
    for client in clients:
        transport = getattr(client, "transport", None)
        close_fn = getattr(client, "close", None) or getattr(
            transport, "close", None
        )
        if callable(close_fn):
            with contextlib.suppress(Exception):
                close_fn()


def _get_pooled_client(location: str) -> modelarmor_v1.ModelArmorClient:
    clean_loc = location.strip()
    with _CLIENT_LOCK:
        client = _CLIENT_POOL.get(clean_loc)
        if client is None:
            endpoint = (
                "modelarmor.googleapis.com"
                if clean_loc == "global"
                else f"modelarmor.{clean_loc}.rep.googleapis.com"
            )
            client = modelarmor_v1.ModelArmorClient(
                client_options={"api_endpoint": endpoint}
            )
            _CLIENT_POOL[clean_loc] = client
    provider.register_reset_hook(reset_model_armor_clients)
    return client


def _first_live_env(keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if (val := os.getenv(key)) and val.strip():
            return val.strip()
    return None


def _match_threat_category(filter_result: Any, hit: Any) -> str | None:
    for field_name, category in _THREAT_CHECKS:
        if (
            field_name in filter_result
            and getattr(filter_result, field_name).match_state == hit
        ):
            return category
    return None


def _inspect_sdp_filter(
    filter_result: Any, hit: Any, current_text: str
) -> tuple[bool, str]:
    if "sdp_filter_result" not in filter_result:
        return False, current_text
    sdp = filter_result.sdp_filter_result
    matched = False
    output_text = current_text
    if sdp.deidentify_result.match_state == hit:
        matched = True
        output_text = sdp.deidentify_result.data.text or output_text
    if sdp.inspect_result.match_state == hit:
        matched = True
    return matched, output_text


def _parse_sanitization(
    sanitization_result: Any,
    original_text: str,
    *,
    fail_closed: bool = True,
) -> models.SafetyScanResult:
    source = "cloud_model_armor"
    hit = modelarmor_v1.FilterMatchState.MATCH_FOUND
    invocation = sanitization_result.invocation_result
    if invocation == modelarmor_v1.InvocationResult.FAILURE or (
        fail_closed and invocation == modelarmor_v1.InvocationResult.PARTIAL
    ):
        raise exceptions.SecurityViolationError("Model Armor call failed.")
    sdp_hit = False
    output_text = original_text
    for filter_result in sanitization_result.filter_results.values():
        category = _match_threat_category(filter_result, hit)
        if category is not None:
            return models.SafetyScanResult(False, "", category, 1.0, source)
        filter_sdp_hit, output_text = _inspect_sdp_filter(
            filter_result, hit, output_text
        )
        sdp_hit = sdp_hit or filter_sdp_hit
    if sdp_hit:
        if output_text == original_text:
            output_text, _ = dlp.RegexPrivacyScrubber().scrub(original_text)
        return models.SafetyScanResult(
            True, output_text, "pii_redacted", 1.0, source
        )
    if sanitization_result.filter_match_state == hit:
        return models.SafetyScanResult(
            False, "", "policy_violation", 1.0, source
        )
    return models.SafetyScanResult(True, original_text, None, 1.0, source)


class CloudModelArmorScanner:
    """Regional Cloud Model Armor adapter implementing SafetyScannerPort."""

    def __init__(
        self,
        *,
        project_id: str | None = None,
        location: str | None = None,
        input_template_id: str | None = None,
        output_template_id: str | None = None,
        fail_closed: bool | None = None,
        client: Any | None = None,
    ) -> None:
        sec = settings.get_security_settings()
        validate_id = settings.validate_gcp_identifier
        raw_proj = (
            project_id
            if project_id is not None
            else (_first_live_env(_SECURITY_PROJ_ENVS) or sec.project_id)
        )
        self._project_id = (
            validate_id(raw_proj, "project_id") if raw_proj else None
        )
        live_loc = location or _first_live_env(_SECURITY_LOC_ENVS)
        _, resolved_loc = settings.resolve_gcp_project_and_location(
            None, live_loc, require_project=False
        )
        self._location = validate_id(resolved_loc, "location")
        self._in_tpl = validate_id(
            input_template_id or sec.input_template_id, "input_template_id"
        )
        self._out_tpl = validate_id(
            output_template_id or sec.output_template_id, "output_template_id"
        )
        self._fail_closed = (
            sec.fail_closed if fail_closed is None else fail_closed
        )
        self._client = client

    def _run_scan(
        self, text: str, *, is_output: bool
    ) -> models.SafetyScanResult:
        try:
            proj, _ = settings.resolve_gcp_project_and_location(
                self._project_id, self._location, require_project=True
            )
            tpl = self._out_tpl if is_output else self._in_tpl
            name = f"projects/{proj}/locations/{self._location}/templates/{tpl}"
            client = self._client or _get_pooled_client(self._location)
            item = modelarmor_v1.DataItem(text=text)
            if is_output:
                out_req = modelarmor_v1.SanitizeModelResponseRequest(
                    name=name, model_response_data=item
                )
                resp: Any = client.sanitize_model_response(
                    request=out_req, timeout=10
                )
            else:
                in_req = modelarmor_v1.SanitizeUserPromptRequest(
                    name=name, user_prompt_data=item
                )
                resp = client.sanitize_user_prompt(request=in_req, timeout=10)
            return _parse_sanitization(
                resp.sanitization_result,
                text,
                fail_closed=self._fail_closed,
            )
        except exceptions.SecurityViolationError:
            raise
        except Exception as exc:
            raise exceptions.SecurityViolationError("Scan failed.") from exc

    def scan_input(self, text: str) -> models.SafetyScanResult:
        """Scans an inbound prompt via Model Armor sanitize_user_prompt."""
        return self._run_scan(text, is_output=False)

    def scan_output(self, text: str) -> models.SafetyScanResult:
        """Scans an outbound response via sanitize_model_response."""
        return self._run_scan(text, is_output=True)
