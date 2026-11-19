# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for Model Armor, Cloud DLP, and SecretManagerStore."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from google.api_core import exceptions as gcp_exceptions
from google.cloud import modelarmor_v1
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
import pytest

from agentops_sdk.domain import exceptions
from agentops_sdk.domain import models
from agentops_sdk.security import CloudModelArmorScanner
from agentops_sdk.security import GoogleDLPScrubber
from agentops_sdk.security import LocalRuleSafetyScanner
from agentops_sdk.security import OutputDLPSanitizer
from agentops_sdk.security import RegexPrivacyScrubber
from agentops_sdk.security import SecretManagerStore
from agentops_sdk.security import check_input
from agentops_sdk.security import check_output
from agentops_sdk.security import dlp
from agentops_sdk.security import model_armor

_HIT = modelarmor_v1.FilterMatchState.MATCH_FOUND


def _ma_resp(
    fr: modelarmor_v1.FilterResult | None = None,
    *,
    invocation: Any = modelarmor_v1.InvocationResult.SUCCESS,
) -> Any:
    sr = modelarmor_v1.SanitizationResult(
        filter_match_state=_HIT
        if fr
        else modelarmor_v1.FilterMatchState.NO_MATCH_FOUND,
        invocation_result=invocation,
        filter_results={"f": fr} if fr else {},
    )
    return SimpleNamespace(sanitization_result=sr)


def test_model_armor_threat_filters() -> None:
    specs = (
        ("rai_filter_result", modelarmor_v1.RaiFilterResult, "rai_violation"),
        (
            "malicious_uri_filter_result",
            modelarmor_v1.MaliciousUriFilterResult,
            "malicious_uri",
        ),
        (
            "csam_filter_filter_result",
            modelarmor_v1.CsamFilterResult,
            "csam_violation",
        ),
        (
            "virus_scan_filter_result",
            modelarmor_v1.VirusScanFilterResult,
            "virus_detected",
        ),
    )
    for field_name, msg_cls, expected_cat in specs:
        fr = modelarmor_v1.FilterResult(
            **{field_name: msg_cls(match_state=_HIT)}
        )
        cli = SimpleNamespace(
            sanitize_user_prompt=lambda _fr=fr, **_kw: _ma_resp(_fr)
        )
        sc = CloudModelArmorScanner(
            project_id="test-proj", location="europe-west4", client=cli
        )
        res = sc.scan_input("payload")
        assert res.is_safe is False and res.violation_category == expected_cat


def test_model_armor_client_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoints: list[str] = []
    monkeypatch.setattr(
        model_armor.modelarmor_v1,
        "ModelArmorClient",
        lambda *, client_options: (
            endpoints.append(client_options["api_endpoint"])
            or SimpleNamespace(close=lambda: None)
        ),
    )
    model_armor._get_pooled_client("global")
    model_armor._get_pooled_client("europe-west4")
    assert endpoints == [
        "modelarmor.googleapis.com",
        "modelarmor.europe-west4.rep.googleapis.com",
    ]


def test_model_armor_partial_and_sdp_inspect_only_masking() -> None:
    inspect_only = modelarmor_v1.FilterResult(
        sdp_filter_result=modelarmor_v1.SdpFilterResult(
            inspect_result=modelarmor_v1.SdpInspectResult(match_state=_HIT)
        )
    )
    cli_out = SimpleNamespace(
        sanitize_model_response=lambda **_kw: _ma_resp(inspect_only)
    )
    sc_out = CloudModelArmorScanner(
        project_id="test-proj", location="europe-west4", client=cli_out
    )
    out = sc_out.scan_output("Call (555) 123-4567 or alice@example.com")
    assert out.is_safe is True and out.violation_category == "pii_redacted"
    assert "alice@example.com" not in out.sanitized_text

    partial_cli = SimpleNamespace(
        sanitize_user_prompt=lambda **_kw: _ma_resp(
            invocation=modelarmor_v1.InvocationResult.PARTIAL
        )
    )
    with pytest.raises(exceptions.SecurityViolationError):
        CloudModelArmorScanner(
            project_id="test-proj", fail_closed=True, client=partial_cli
        ).scan_input("hi")
    res_open = CloudModelArmorScanner(
        project_id="test-proj", fail_closed=False, client=partial_cli
    ).scan_input("hi")
    assert res_open.is_safe is True


def test_security_guard_helpers_and_otel_spans(
    otel_span_exporter: InMemorySpanExporter,
) -> None:
    sc = LocalRuleSafetyScanner()
    dec_in = check_input("Clean prompt", scanner=sc)
    assert dec_in.verdict == models.SecurityVerdict.ALLOW
    dec_out = check_output("Reach me at bob@example.com", scanner=sc)
    assert dec_out.verdict == models.SecurityVerdict.SANITIZED
    spans = otel_span_exporter.get_finished_spans()
    assert len(spans) == 2
    assert spans[0].attributes["safety.verdict"] == "ALLOW"
    assert spans[1].attributes["safety.verdict"] == "SANITIZED"

    fail_cli = SimpleNamespace(
        sanitize_user_prompt=lambda **_kw: _ma_resp(
            invocation=modelarmor_v1.InvocationResult.FAILURE
        )
    )
    with pytest.raises(exceptions.SecurityViolationError):
        CloudModelArmorScanner(
            project_id="test-proj", client=fail_cli
        ).scan_input("hi")


async def test_dlp_chunking_backtracking_and_output_sanitizer(
    otel_span_exporter: InMemorySpanExporter,
) -> None:
    big_text = "a" + ("😀" * 110_000) + " a@b.com"
    chunks = dlp._chunk_utf8_text(big_text)
    assert len(chunks) == 2
    assert all(len(c.encode("utf-8")) <= dlp._MAX_CHUNK_BYTES for c in chunks)
    assert "".join(chunks) == big_text

    fake_dlp = SimpleNamespace(
        deidentify_content=lambda *, request, **_kw: SimpleNamespace(
            item=SimpleNamespace(
                value=RegexPrivacyScrubber().scrub(str(request.item.value))[0]
            )
        )
    )
    scrubber = GoogleDLPScrubber(
        project_id="test-proj",
        inspect_template_name="projects/p/locations/l/inspectTemplates/i",
        deidentify_template_name="projects/p/locations/l/deidentifyTemplates/d",
        client=fake_dlp,
    )
    scrubbed, modified = scrubber.scrub(big_text)
    assert modified is True and "a@b.com" not in scrubbed
    assert scrubber._build_request("hello").inspect_template_name.endswith("/i")

    sanitizer = OutputDLPSanitizer(scrubber=RegexPrivacyScrubber())
    with trace.get_tracer("test").start_as_current_span("parent"):
        assert "[EMAIL_ADDRESS]" in sanitizer.scrub("user@example.com")
        assert "[US_SOCIAL_SECURITY_NUMBER]" in await sanitizer.scrub_text(
            "123-45-6789"
        )
    spans = otel_span_exporter.get_finished_spans()
    assert spans[0].attributes["agentops.privacy.pii_scrubbed"] is True


def test_secret_manager_delete_evicts_all_versions() -> None:
    calls: list[str] = []

    def _add(*, request: dict[str, Any], **_kw: Any) -> SimpleNamespace:
        calls.append("add")
        if len(calls) == 1:
            raise gcp_exceptions.NotFound("secret")
        return SimpleNamespace(name=f"{request['parent']}/versions/1")

    def _access(*, request: dict[str, Any], **_kw: Any) -> SimpleNamespace:
        calls.append(f"get:{request['name'].rsplit('/', 1)[-1]}")
        return SimpleNamespace(payload=SimpleNamespace(data=b"val-v"))

    client = SimpleNamespace(
        add_secret_version=_add,
        create_secret=lambda **_kw: calls.append("create"),
        access_secret_version=_access,
        delete_secret=lambda **_kw: calls.append("delete"),
    )
    store = SecretManagerStore(project_id="test-proj", client=client)
    assert store.put_secret("new-sec", "val").endswith("/versions/1")
    assert store.get_secret("new-sec", version="1") == "val-v"
    assert store.get_secret("new-sec", version="2") == "val-v"
    assert store.delete_secret("new-sec") is True
    for ver in ("latest", "1", "2"):
        assert store._cache.get(("test-proj", "new-sec", ver)) is None
    store.evict("new-sec", version="latest")
    with pytest.raises(exceptions.SecretAccessError):
        store.get_secret("new-sec", version="bad-ver")
