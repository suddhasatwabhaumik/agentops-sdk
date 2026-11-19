# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Adversarial security CWE regressions: files, DLP, XSS, and templates."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentops_sdk.audit import LocalFileAuditAdapter
from agentops_sdk.domain import exceptions
from agentops_sdk.evaluation import ExperimentConfig
from agentops_sdk.evaluation import ExperimentEngine
from agentops_sdk.evaluation import QualityGateSessionReporter
from agentops_sdk.evaluation import QualityGateTestRecord
from agentops_sdk.evaluation import reporting
from agentops_sdk.prompts import LocalYAMLPromptAdapter
from agentops_sdk.prompts import PromptRegistry
from agentops_sdk.pytest_plugin import _safe_report_path
from agentops_sdk.security import CloudModelArmorScanner
from agentops_sdk.security import GoogleDLPScrubber
from agentops_sdk.security import RegexPrivacyScrubber
from agentops_sdk.security import SecurityGuard
from agentops_sdk.telemetry import LocalFileTelemetryExporter
from agentops_sdk.telemetry import _sink_helpers


def test_cwe_parent_symlink_nul_and_o_nofollow(tmp_path: Path) -> None:
    base = tmp_path / "base"
    outside = tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    outside_file = outside / "secret.txt"
    outside_file.write_text("sensitive", encoding="utf-8")
    os.symlink(outside, base / "symdir")
    os.symlink(outside_file, base / "link.jsonl")

    for bad_path in (
        base / "symdir" / "a.jsonl",
        base / "link.jsonl",
        str(base / "bad\x00.jsonl"),
    ):
        with pytest.raises(ValueError):
            LocalFileAuditAdapter(bad_path, base_dir=base)
        with pytest.raises(ValueError):
            LocalFileTelemetryExporter(bad_path, base_dir=base)

    pdir = base / "prompts"
    pdir.mkdir()
    os.symlink(outside_file, pdir / "leak.yaml")
    with pytest.raises(exceptions.PromptRegistryError, match="Symlink"):
        LocalYAMLPromptAdapter(str(pdir)).get_prompt("leak")
    with pytest.raises(ValueError):
        _safe_report_path("../escape.md", base)
    with pytest.raises(OSError):
        _sink_helpers._SecureRotatingFileHandler._secure_opener(
            str(base / "link.jsonl"), 0
        )


def test_cwe_dlp_continuation_backtracking_and_model_armor() -> None:
    big_text = "a" + ("😀" * 110_000) + " a@b.com"
    seen: list[int] = []
    dlp_cli = SimpleNamespace(
        deidentify_content=lambda *, request, **_kw: (
            seen.append(len(request.item.value.encode("utf-8")))
            or SimpleNamespace(
                item=SimpleNamespace(
                    value=RegexPrivacyScrubber().scrub(str(request.item.value))[
                        0
                    ]
                )
            )
        )
    )
    scrubber = GoogleDLPScrubber(project_id="test-proj", client=dlp_cli)
    out, modified = scrubber.scrub(big_text)
    assert modified is True and "a@b.com" not in out
    assert len(seen) >= 2 and all(b <= 400_000 for b in seen)

    broken_ma = CloudModelArmorScanner(
        project_id="test-proj",
        client=SimpleNamespace(
            sanitize_user_prompt=lambda **_kw: (_ for _ in ()).throw(
                RuntimeError("MA down")
            )
        ),
    )
    with pytest.raises(exceptions.SecurityViolationError):
        broken_ma.scan_input("hello")
    assert (
        SecurityGuard(scanner=broken_ma).check_input("hello").allowed is False
    )


def test_cwe_reporting_xss_and_url_injection() -> None:
    assert reporting._safe_url("https://console.cloud.google.com/run/1") is True
    for bad_url in (
        "javascript:alert(1)",
        "https://user:pass@evil.com/path",
        "https://evil.com/x) onclick=alert(1)",
        "https://evil.com/[md]",
    ):
        assert reporting._safe_url(bad_url) is False

    rep = QualityGateSessionReporter()
    rep.add_record(
        QualityGateTestRecord(
            test_name="<script>alert(1)</script> [click](https://evil)",
            eval_mode="pointwise",
            metrics={"groundedness": 0.9},
            thresholds={"groundedness": 0.8},
            is_passing=True,
            console_url="https://user:pass@evil.com/leak",
        )
    )
    md = rep.generate_markdown()
    assert "<script>" not in md
    assert "&lt;script&gt;" in md
    assert "user:pass@evil.com" not in md


def test_cwe_sandboxed_template_dos_and_unconfined_output(
    tmp_path: Path,
) -> None:
    reg = PromptRegistry(backend="in_memory")
    for bad_tmpl in (
        "{{ ''.__class__.__mro__ }}",
        "{{ 999999 ** 999999 }}",
        "{{ 'a' * 1000000 }}",
        "{% for i in range(100000) %}x{% endfor %}",
    ):
        with pytest.raises(exceptions.PromptRegistryError):
            reg.format_prompt(bad_tmpl)

    cfg = ExperimentConfig(
        name="exp",
        prompt_template="{prompt}",
        variants={"a": {}},
        dataset=[{"prompt": "p", "context": "c", "response": "c"}],
    )
    eng = ExperimentEngine(cfg)
    for bad_out in ("/etc/passwd", "../escape.json"):
        with pytest.raises(ValueError):
            eng.run(output_path=bad_out)
    safe_out = tmp_path / "exp.json"
    eng.run(output_path=str(safe_out))
    assert safe_out.exists()
