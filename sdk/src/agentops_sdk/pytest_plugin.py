# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Pytest plugin for AgentOps CI/CD evaluation quality gates."""

from __future__ import annotations

import os
import sys
from typing import Any

import pytest

from agentops_sdk.config import settings
from agentops_sdk.evaluation import in_memory
from agentops_sdk.evaluation import reporting

QualityGateSessionReporter = reporting.QualityGateSessionReporter
QualityGateTestRecord = reporting.QualityGateTestRecord
session_reporter = reporting.session_reporter

__all__ = [
    "QualityGateSessionReporter",
    "QualityGateTestRecord",
    "session_reporter",
]

_VALID_MODES = frozenset({"in_memory", "heuristic", "vertex"})
_CLI_SPECS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "--eval-mode",
        {
            "action": "store",
            "default": "in_memory",
            "choices": ["in_memory", "heuristic", "vertex"],
            "help": "Evaluation mode: 'in_memory', 'heuristic', or 'vertex'.",
        },
    ),
    (
        "--fail-under-groundedness",
        {
            "action": "store",
            "type": float,
            "default": 0.85,
            "help": "Minimum Groundedness threshold (default: 0.85).",
        },
    ),
    (
        "--fail-under-relevance",
        {
            "action": "store",
            "type": float,
            "default": 0.80,
            "help": "Minimum QA Relevance threshold (default: 0.80).",
        },
    ),
    (
        "--fail-under-tool-quality",
        {
            "action": "store",
            "type": float,
            "default": 0.80,
            "help": "Minimum tool quality threshold (default: 0.80).",
        },
    ),
    (
        "--fail-under-task-success",
        {
            "action": "store",
            "type": float,
            "default": 0.80,
            "help": "Minimum task success threshold (default: 0.80).",
        },
    ),
    (
        "--eval-report-path",
        {
            "action": "store",
            "default": "eval_quality_gate_summary.md",
            "help": "Relative path under pytest root for the markdown report.",
        },
    ),
    (
        "--eval-project",
        {
            "action": "store",
            "default": None,
            "help": "GCP project ID for Vertex AI evaluation runs.",
        },
    ),
    (
        "--eval-location",
        {
            "action": "store",
            "default": None,
            "help": "GCP region for Vertex AI evaluation runs.",
        },
    ),
)


def _safe_report_path(raw_path: str, root: Any) -> str:
    if "\x00" in raw_path or not raw_path.strip():
        raise ValueError(f"Invalid --eval-report-path: {raw_path!r}")
    base = (
        os.fspath(root) if isinstance(root, str | os.PathLike) else os.getcwd()
    )
    root_dir = os.path.realpath(base)
    joined = (
        raw_path
        if os.path.isabs(raw_path)
        else os.path.join(root_dir, raw_path)
    )
    target = os.path.realpath(joined)
    if os.path.commonpath([root_dir, target]) != root_dir:
        raise ValueError(
            f"Report path {raw_path!r} escapes project root {root_dir!r}."
        )
    return target


def _validate_mode(mode: Any) -> str:
    clean = str(mode)
    if clean not in _VALID_MODES:
        raise ValueError(
            f"Invalid --eval-mode {clean!r}; expected {sorted(_VALID_MODES)}."
        )
    return clean


def pytest_addoption(parser: pytest.Parser) -> None:
    """Registers CLI options for AgentOps quality gate evaluation."""
    group = parser.getgroup(
        "agentops-eval", "AgentOps CI/CD Evaluation Quality Gates"
    )
    for opt, kw in _CLI_SPECS:
        group.addoption(opt, **kw)


def pytest_configure(config: pytest.Config) -> None:
    """Registers markers and configures the session reporter."""
    markers = (
        "quality_gate: Mark test as an AgentOps evaluation quality gate.",
        "vertex_eval: Mark test as requiring Vertex AI Evaluation Service.",
        "trajectory_eval: Mark test as a multi-turn trajectory evaluation.",
    )
    for marker in markers:
        config.addinivalue_line("markers", marker)
    mode = _validate_mode(config.getoption("--eval-mode", default="in_memory"))
    raw_rp = str(
        config.getoption(
            "--eval-report-path", default="eval_quality_gate_summary.md"
        )
    )
    rp = _safe_report_path(raw_rp, getattr(config, "rootpath", None))
    raw_proj = config.getoption("--eval-project", default=None)
    raw_loc = config.getoption("--eval-location", default=None)
    proj, loc = settings.resolve_gcp_project_and_location(
        raw_proj, raw_loc, require_project=False
    )
    reporting.session_reporter.clear()
    reporting.session_reporter.configure(
        eval_mode=mode,
        project=proj,
        location=loc,
        report_path=rp,
        default_groundedness=config.getoption(
            "--fail-under-groundedness", default=0.85
        ),
        default_relevance=config.getoption(
            "--fail-under-relevance", default=0.80
        ),
        default_tool_quality=config.getoption(
            "--fail-under-tool-quality", default=0.80
        ),
        default_task_success=config.getoption(
            "--fail-under-task-success", default=0.80
        ),
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Exports the confined Markdown summary report on session finish."""
    del exitstatus
    rep = reporting.session_reporter
    if not rep.records:
        return
    root_attr = getattr(getattr(session, "config", None), "rootpath", None)
    out_path = _safe_report_path(rep.report_path, root_attr)
    md = rep.generate_markdown()
    try:
        if parent_dir := os.path.dirname(out_path):
            os.makedirs(parent_dir, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(md)
    except OSError as err:
        sys.stderr.write(
            f"AgentOps plugin failed to write report to {out_path}: {err}\n"
        )


@pytest.fixture(scope="session")
def eval_mode(pytestconfig: pytest.Config) -> str:
    """Provides the validated active evaluation mode."""
    return _validate_mode(
        pytestconfig.getoption("--eval-mode", default="in_memory")
    )


@pytest.fixture(scope="session")
def quality_gate_evaluator(pytestconfig: pytest.Config) -> Any:
    """Provides a configured EvaluatorPort instance for quality gates."""
    mode = _validate_mode(
        pytestconfig.getoption("--eval-mode", default="in_memory")
    )
    raw_proj = pytestconfig.getoption("--eval-project", default=None)
    raw_loc = pytestconfig.getoption("--eval-location", default=None)
    proj, loc = settings.resolve_gcp_project_and_location(
        raw_proj, raw_loc, require_project=False
    )
    if mode == "vertex":
        return in_memory.get_evaluator(
            "vertex_ai", project_id=proj, location=loc
        )
    return in_memory.get_evaluator(mode)
