# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Thread-safe quality gate reporting and Markdown export."""

from __future__ import annotations

from collections.abc import Iterator
import contextlib
import contextvars
import dataclasses
import re
import threading
from typing import Any
from urllib import parse as urllib_parse

from agentops_sdk.evaluation import suite_builder

normalize_score = suite_builder.normalize_score
extract_metric_stats = suite_builder.extract_metric_stats
build_suite_result = suite_builder.build_suite_result

_CONFIG_KEYS = frozenset(
    {
        "eval_mode",
        "project",
        "location",
        "report_path",
        "default_groundedness",
        "default_relevance",
        "default_tool_quality",
        "default_task_success",
    }
)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")
_FORBIDDEN_URL_CHARS = frozenset("()[]<>\"'\\")
_MAX_URL_LENGTH = 2048


def _esc(value: str) -> str:
    cleaned = _CONTROL_CHARS_RE.sub(" ", value)
    cleaned = cleaned.replace("|", "\\|").replace("`", "'")
    cleaned = cleaned.replace("<", "&lt;").replace(">", "&gt;")
    return cleaned.replace("[", "\\[").replace("]", "\\]").strip()


def _safe_url(url: str | None) -> bool:
    raw = (url or "").strip()
    if not raw or len(raw) > _MAX_URL_LENGTH:
        return False
    if any(
        ch.isspace()
        or ord(ch) < 32
        or ord(ch) == 127
        or ch in _FORBIDDEN_URL_CHARS
        for ch in raw
    ):
        return False
    parsed = urllib_parse.urlparse(raw)
    return (
        parsed.scheme.lower() == "https"
        and bool(parsed.netloc)
        and not parsed.username
        and not parsed.password
    )


@dataclasses.dataclass(frozen=True, slots=True)
class QualityGateTestRecord:
    """Immutable record of a single quality gate assertion."""

    test_name: str
    eval_mode: str
    metrics: dict[str, float]
    thresholds: dict[str, float]
    is_passing: bool
    explanations: dict[str, str] = dataclasses.field(default_factory=dict)
    console_url: str | None = None
    provider: str = "in_memory"


class QualityGateSessionReporter:
    """Thread-safe collector for CI/CD quality gate results."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[QualityGateTestRecord] = []
        self.eval_mode = "in_memory"
        self.project: str | None = None
        self.location = "europe-west4"
        self.report_path = "eval_quality_gate_summary.md"
        self.default_groundedness = 0.85
        self.default_relevance = 0.80
        self.default_tool_quality = 0.80
        self.default_task_success = 0.80

    @property
    def records(self) -> list[QualityGateTestRecord]:
        """Returns a thread-safe snapshot of recorded evaluations."""
        with self._lock:
            return list(self._records)

    def add_record(self, record: QualityGateTestRecord) -> None:
        """Appends a QualityGateTestRecord under lock."""
        with self._lock:
            self._records.append(record)

    def clear(self) -> None:
        """Clears all recorded quality gate results."""
        with self._lock:
            self._records.clear()

    def reset(self) -> None:
        """Clears records and restores default reporter configuration."""
        with self._lock:
            self._records.clear()
            self.eval_mode = "in_memory"
            self.project = None
            self.location = "europe-west4"
            self.report_path = "eval_quality_gate_summary.md"
            self.default_groundedness = 0.85
            self.default_relevance = 0.80
            self.default_tool_quality = 0.80
            self.default_task_success = 0.80

    def configure(self, **kwargs: Any) -> None:
        """Atomically updates reporter configuration under lock."""
        with self._lock:
            for key, val in kwargs.items():
                if key in _CONFIG_KEYS and val is not None:
                    setattr(self, key, val)

    def generate_markdown(self) -> str:
        """Formats recorded quality gate results into Markdown."""
        with self._lock:
            recs = list(self._records)
            mode = _esc(self.eval_mode)
            proj = _esc(self.project or "N/A (Local)")
            loc = _esc(self.location)
        passed = sum(1 for rec in recs if rec.is_passing)
        verdict = "PASSED" if passed == len(recs) else "FAILED"
        header = [
            "# AgentOps Quality Gate Evaluation Summary",
            "",
            f"**Mode**: `{mode}` | **Project**: `{proj}` | **Region**: `{loc}`",
            f"**Verdict**: **{verdict}** ({passed}/{len(recs)})",
            "",
        ]
        if not recs:
            return "\n".join([*header, "_No quality gates executed._"]) + "\n"
        body = [
            *header,
            *self._build_table(recs),
            *self._build_failures(recs),
            *self._build_links(recs),
        ]
        return "\n".join(body) + "\n"

    def _build_table(self, recs: list[QualityGateTestRecord]) -> list[str]:
        rows = [
            "| Test Name | Mode | Provider | Metrics | Verdict |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
        for rec in recs:
            metric_bits = [
                f"{_esc(k)}={v:.2f}(>={rec.thresholds.get(k, 0.0):.2f})"
                for k, v in sorted(rec.metrics.items())
            ]
            verdict = "PASS" if rec.is_passing else "FAIL"
            metrics_col = ", ".join(metric_bits) or "-"
            c1 = _esc(rec.test_name)
            c2 = _esc(rec.eval_mode)
            c3 = _esc(rec.provider)
            rows.append(f"| `{c1}` | {c2} | {c3} | {metrics_col} | {verdict} |")
        return rows

    def _build_failures(self, recs: list[QualityGateTestRecord]) -> list[str]:
        failed_recs = [rec for rec in recs if not rec.is_passing]
        if not failed_recs:
            return []
        lines = ["", "### Diagnostic Failure Analysis", ""]
        for rec in failed_recs:
            lines.append(f"#### `{_esc(rec.test_name)}`")
            for name, threshold in rec.thresholds.items():
                score = rec.metrics.get(name)
                if score is None or score < threshold:
                    expl = _esc(
                        rec.explanations.get(name, "Threshold not met.")
                    )
                    status = "MISSING" if score is None else f"{score:.2f}"
                    lines.append(
                        f"- **{_esc(name)}**: {status} < {threshold:.2f} "
                        f"({expl})"
                    )
            lines.append("")
        return lines

    def _build_links(self, recs: list[QualityGateTestRecord]) -> list[str]:
        raw = [
            url.strip()
            for rec in recs
            if (url := rec.console_url) and _safe_url(url)
        ]
        urls = list(dict.fromkeys(raw))
        if not urls:
            return []
        return [
            "### Vertex AI Evaluation Experiments",
            "",
            *[f"- [Vertex AI Console]({url})" for url in urls],
        ]


session_reporter = QualityGateSessionReporter()
_ACTIVE_REPORTER: contextvars.ContextVar[QualityGateSessionReporter | None] = (
    contextvars.ContextVar("agentops_quality_gate_reporter", default=None)
)


def get_session_reporter() -> QualityGateSessionReporter:
    """Returns ContextVar-scoped reporter or global session_reporter."""
    return _ACTIVE_REPORTER.get() or session_reporter


@contextlib.contextmanager
def use_session_reporter(
    reporter: QualityGateSessionReporter,
) -> Iterator[QualityGateSessionReporter]:
    """Temporarily binds an isolated QualityGateSessionReporter."""
    token = _ACTIVE_REPORTER.set(reporter)
    try:
        yield reporter
    finally:
        _ACTIVE_REPORTER.reset(token)
