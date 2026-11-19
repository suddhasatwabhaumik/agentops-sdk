# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for AgentOps pytest plugin CLI, confinement, and fixtures."""

from __future__ import annotations

from collections.abc import Iterator
import importlib
import os
from pathlib import Path
import sys
from unittest.mock import MagicMock

import pytest

from agentops_sdk.evaluation import reporting
from agentops_sdk.ports import evaluation as eval_ports
from agentops_sdk.pytest_plugin import QualityGateSessionReporter
from agentops_sdk.pytest_plugin import QualityGateTestRecord
from agentops_sdk.pytest_plugin import _safe_report_path
from agentops_sdk.pytest_plugin import _validate_mode
from agentops_sdk.pytest_plugin import pytest_addoption
from agentops_sdk.pytest_plugin import pytest_configure
from agentops_sdk.pytest_plugin import pytest_sessionfinish
from agentops_sdk.pytest_plugin import session_reporter


@pytest.fixture
def isolated_global_reporter() -> Iterator[None]:
    """Resets global session_reporter safely via public reset()."""
    session_reporter.reset()
    try:
        yield
    finally:
        session_reporter.reset()


def test_pytest_addoption_registers_cli_flags() -> None:
    parser = MagicMock()
    group = MagicMock()
    parser.getgroup.return_value = group

    pytest_addoption(parser)

    parser.getgroup.assert_called_once_with(
        "agentops-eval", "AgentOps CI/CD Evaluation Quality Gates"
    )
    names = [call[0][0] for call in group.addoption.call_args_list]
    for flag in (
        "--eval-mode",
        "--fail-under-groundedness",
        "--fail-under-relevance",
        "--fail-under-tool-quality",
        "--fail-under-task-success",
        "--eval-report-path",
        "--eval-project",
        "--eval-location",
    ):
        assert flag in names


def test_mode_and_report_path_confinement(tmp_path: Path) -> None:
    for mode in ("in_memory", "heuristic", "vertex"):
        assert _validate_mode(mode) == mode
    with pytest.raises(ValueError):
        _validate_mode("mock")

    ok = _safe_report_path("reports/summary.md", tmp_path)
    assert ok == str((tmp_path / "reports" / "summary.md").resolve())
    for bad in ("../escape.md", "/etc/passwd", "bad\x00.md", "   "):
        with pytest.raises(ValueError):
            _safe_report_path(bad, tmp_path)

    outside = tmp_path.parent / "outside.md"
    link = tmp_path / "link.md"
    os.symlink(outside, link)
    with pytest.raises(ValueError, match="escapes project root"):
        _safe_report_path("link.md", tmp_path)


def test_pytest_configure_and_sessionfinish(
    tmp_path: Path, isolated_global_reporter: None
) -> None:
    del isolated_global_reporter
    config = MagicMock()
    config.rootpath = tmp_path
    opts = {
        "--eval-mode": "heuristic",
        "--fail-under-groundedness": 0.90,
        "--fail-under-relevance": 0.85,
        "--fail-under-tool-quality": 0.80,
        "--fail-under-task-success": 0.82,
        "--eval-report-path": "nested/eval_summary.md",
        "--eval-project": "unit-proj",
        "--eval-location": "europe-west4",
    }
    config.getoption.side_effect = lambda k, default=None: opts.get(k, default)

    pytest_configure(config)
    assert config.addinivalue_line.call_count == 3
    assert session_reporter.eval_mode == "heuristic"
    assert session_reporter.default_groundedness == 0.90

    session_reporter.add_record(
        QualityGateTestRecord(
            test_name="test_gate",
            eval_mode="pointwise",
            metrics={"groundedness": 0.95},
            thresholds={"groundedness": 0.90},
            is_passing=True,
        )
    )
    session = MagicMock()
    session.config.rootpath = tmp_path
    pytest_sessionfinish(session, exitstatus=0)
    out = tmp_path / "nested" / "eval_summary.md"
    assert out.exists()
    assert "test_gate" in out.read_text(encoding="utf-8")


def test_use_session_reporter_context_and_fixtures(
    eval_mode: str, quality_gate_evaluator: object
) -> None:
    assert eval_mode in ("in_memory", "heuristic", "vertex")
    assert isinstance(quality_gate_evaluator, eval_ports.EvaluatorPort)
    custom = QualityGateSessionReporter()
    with reporting.use_session_reporter(custom) as active:
        assert active is custom
        assert reporting.get_session_reporter() is custom
    assert reporting.get_session_reporter() is reporting.session_reporter


def test_base_wheel_isolation_without_optional_eval_deps() -> None:
    blocked = ("pandas", "tqdm", "numpy")
    targets = ("agentops_sdk.pytest_plugin", *blocked)
    saved = {
        k: sys.modules.pop(k)
        for k in list(sys.modules)
        if k.split(".")[0] in blocked or k == "agentops_sdk.pytest_plugin"
    }

    class _BlockOptional:
        def find_spec(
            self, fullname: str, path: object = None, target: object = None
        ) -> None:
            del path, target
            if fullname.split(".")[0] in blocked:
                raise ImportError(f"Blocked optional dep: {fullname}")
            return None

    blocker = _BlockOptional()
    sys.meta_path.insert(0, blocker)
    try:
        mod = importlib.import_module("agentops_sdk.pytest_plugin")
        assert hasattr(mod, "pytest_configure")
        assert hasattr(mod, "quality_gate_evaluator")
    finally:
        sys.meta_path.remove(blocker)
        for k in list(sys.modules):
            if k.split(".")[0] in blocked or k in targets:
                sys.modules.pop(k, None)
        sys.modules.update(saved)
