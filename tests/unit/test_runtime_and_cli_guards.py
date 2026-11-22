# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for runtime bounds, eval warnings, CLI guards, and git diff."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from agentops_sdk.domain.lifecycle import BoundedTTLCache
from agentops_sdk.evaluation import VertexAIEvalConfig
from agentops_sdk.evaluation import _task_runner
from tools.observability import _sink_ops
from tools.observability import log_router_admin
from tools.release_notes import consolidate


@pytest.mark.parametrize("bad_maxsize", [0, -1, -5, True, False, 1.5, 2.0])
def test_bounded_ttl_cache_rejects_invalid_maxsize(
    bad_maxsize: Any,
) -> None:
    with pytest.raises(ValueError, match="maxsize must be a positive int"):
        BoundedTTLCache(maxsize=bad_maxsize)


def test_run_eval_task_instance_warns_only_when_autorater_rejected(
    caplog: pytest.LogCaptureFixture,
) -> None:
    df = pd.DataFrame([{"prompt": "p", "response": "r"}])
    cfg = VertexAIEvalConfig(
        project_id="test-proj",
        location="europe-west4",
        judge_model="gemini-3.8-flash",
    )
    ar_cfg = SimpleNamespace(autorater_model="projects/p/models/gemini")

    class _NoAutoraterTask:
        def __init__(
            self,
            dataset: Any,
            metrics: Any,
            experiment: Any = None,
            metric_column_mapping: Any = None,
            output_uri_prefix: str = "",
        ) -> None:
            del dataset, metrics, experiment
            del metric_column_mapping, output_uri_prefix

        def evaluate(self, **_kw: Any) -> str:
            return "no-ar-ok"

    class _AcceptingTask:
        def __init__(self, **_kw: Any) -> None:
            pass

        def evaluate(self, **_kw: Any) -> str:
            return "ar-ok"

    with caplog.at_level(logging.WARNING, logger=_task_runner._LOGGER.name):
        res1 = _task_runner.run_eval_task_instance(
            cfg, df, ["groundedness"], (_NoAutoraterTask, ar_cfg, False), None
        )
    assert res1 == "no-ar-ok"
    assert any(
        "does not accept autorater_config" in r.message
        and r.levelno == logging.WARNING
        for r in caplog.records
    )

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=_task_runner._LOGGER.name):
        res2 = _task_runner.run_eval_task_instance(
            cfg, df, ["groundedness"], (_AcceptingTask, ar_cfg, False), None
        )
    assert res2 == "ar-ok"
    assert caplog.records == []


def test_log_router_admin_file_requires_output_file_and_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    constructed: list[Any] = []
    monkeypatch.setattr(
        log_router_admin,
        "LogRouterManager",
        lambda **_kw: constructed.append(True),
    )
    with pytest.raises(SystemExit) as exc_info:
        log_router_admin.main(
            ["--project", "test-proj", "export", "--dest", "file"]
        )
    assert exc_info.value.code == 2
    assert (
        "--destination file requires --output-file." in capsys.readouterr().err
    )
    assert constructed == []

    log_router_admin.main(["setup", "--project-id", "test-proj", "--dry-run"])
    out = capsys.readouterr().out
    assert "[DRY-RUN] BigQuery Sink" in out
    assert "projects/test-proj/datasets/agentops_observability" in out
    assert "[DRY-RUN] GCS Sink" in out
    assert "storage.googleapis.com/test-proj-agentops-observability-logs" in out
    assert constructed == []


@pytest.mark.parametrize(
    ("dest", "expected"),
    [
        ("bq", (True, False)),
        ("gcs", (False, True)),
        ("both", (True, True)),
        ("file", (False, False)),
    ],
)
def test_log_router_admin_dest_dispatch_and_subparser_project_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    dest: str,
    expected: tuple[bool, bool],
) -> None:
    calls: list[dict[str, Any]] = []

    class _FakeManager:
        def __init__(self, **kw: Any) -> None:
            self.init_kw = kw

        def export_logs(self, **kw: Any) -> dict[str, Any]:
            calls.append(kw)
            return {"ok": True}

    monkeypatch.setattr(log_router_admin, "LogRouterManager", _FakeManager)
    extra = (
        ["--output-file", str(tmp_path / "out.jsonl")] if dest == "file" else []
    )
    log_router_admin.main(
        ["export", "--project-id", "test-proj", "--dest", dest, *extra]
    )
    assert len(calls) == 1
    assert (calls[0]["target_bq"], calls[0]["target_gcs"]) == expected
    parser = _sink_ops.build_cli_parser(None, "europe-west4")
    parsed = parser.parse_args(["list-sinks", "--project-id", "sub-proj"])
    assert parsed.project == "sub-proj"


def test_consolidate_resolve_diff_base_steps_back_when_shas_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_git_with_tag(args: list[str]) -> str:
        if args[:2] == ["rev-parse", "--verify"]:
            return "abc1234\n"
        if args[:3] == ["describe", "--tags", "--abbrev=0"]:
            return "v0.9.0\n"
        raise AssertionError(f"Unexpected git args: {args}")

    monkeypatch.setattr(consolidate, "_run_git", _fake_git_with_tag)
    assert consolidate._resolve_diff_base("abc1234", "abc1234") == "v0.9.0"

    def _fake_git_no_tag(args: list[str]) -> str:
        if args[:2] == ["rev-parse", "--verify"]:
            return "abc1234\n"
        if args[:3] == ["describe", "--tags", "--abbrev=0"]:
            raise consolidate.subprocess.CalledProcessError(128, "git")
        raise AssertionError(f"Unexpected git args: {args}")

    monkeypatch.setattr(consolidate, "_run_git", _fake_git_no_tag)
    assert consolidate._resolve_diff_base("abc1234", "HEAD") == "HEAD~1"
