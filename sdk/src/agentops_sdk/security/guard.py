# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Local safety scanner and fail-closed SecurityGuard policy engine."""

from __future__ import annotations

import logging
import re
import time

from opentelemetry import trace

from agentops_sdk import ports
from agentops_sdk.config import settings
from agentops_sdk.domain import models
from agentops_sdk.security import dlp
from agentops_sdk.security import model_armor
from agentops_sdk.telemetry import provider

logger = logging.getLogger(__name__)

_INJECTION_RE = re.compile(
    r"(?:ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)\s+"
    r"(?:instructions|prompts|rules|directives)|disregard\s+(?:all\s+|"
    r"previous\s+|prior\s+)?(?:instructions|prompts|rules|directives)|"
    r"system\s+prompt(?:\s+(?:override|leak|extraction))?|reveal\s+(?:your\s+|"
    r"the\s+)?system\s+prompt|\b(?:dan\s+mode|do\s+anything\s+now|jailbreak|"
    r"bypass\s+safety|override\s+safety|ignore\s+rules|developer\s+mode\s+"
    r"enabled)\b|<\s*(?:system|admin_override)\s*>)",
    re.IGNORECASE,
)


class LocalRuleSafetyScanner:
    """Deterministic offline safety scanner for local testing or fallback."""

    def scan_input(self, text: str) -> models.SafetyScanResult:
        """Detects prompt-injection and jailbreak patterns offline."""
        if _INJECTION_RE.search(text):
            return models.SafetyScanResult(
                False, "", "prompt_injection", 0.95, "local_rules"
            )
        return models.SafetyScanResult(True, text, None, 1.0, "local_rules")

    def scan_output(self, text: str) -> models.SafetyScanResult:
        """Rejects injection payloads and scrubs PII offline via regex DLP."""
        if _INJECTION_RE.search(text):
            return models.SafetyScanResult(
                False, "", "prompt_injection", 0.95, "local_rules"
            )
        masked, modified = dlp.RegexPrivacyScrubber().scrub(text)
        category: str | None = "pii_redacted" if modified else None
        return models.SafetyScanResult(
            True, masked, category, 1.0, "local_rules"
        )


def _to_decision(
    scan: models.SafetyScanResult, original_text: str, elapsed_ms: float
) -> models.DecisionResult:
    if not scan.is_safe:
        category = scan.violation_category or "policy_violation"
        return models.DecisionResult(
            False,
            models.SecurityVerdict.BLOCK,
            "",
            f"Blocked: {category}",
            category,
            elapsed_ms,
        )
    if (
        scan.sanitized_text != original_text
        or scan.violation_category == "pii_redacted"
    ):
        return models.DecisionResult(
            True,
            models.SecurityVerdict.SANITIZED,
            scan.sanitized_text,
            "Sanitized.",
            scan.violation_category,
            elapsed_ms,
        )
    return models.DecisionResult(
        True,
        models.SecurityVerdict.ALLOW,
        scan.sanitized_text,
        "Safe.",
        None,
        elapsed_ms,
    )


class SecurityGuard:
    """Enforces cloud-first content safety checks with fail-closed policy."""

    def __init__(
        self,
        scanner: ports.security.SafetyScannerPort | None = None,
        *,
        fail_closed: bool | None = None,
    ) -> None:
        fc_default = settings.get_security_settings().fail_closed
        self._fail_closed = fc_default if fail_closed is None else fail_closed
        self._scanner: ports.security.SafetyScannerPort = (
            model_armor.CloudModelArmorScanner(fail_closed=self._fail_closed)
            if scanner is None
            else scanner
        )
        self._fallback = LocalRuleSafetyScanner()

    def _on_error(
        self, text: str, direction: str, elapsed_ms: float
    ) -> models.DecisionResult:
        logger.warning(
            "Scanner failed on %s; fail_closed=%s.",
            direction,
            self._fail_closed,
        )
        if self._fail_closed:
            return models.DecisionResult(
                False,
                models.SecurityVerdict.BLOCK,
                "",
                "Unavailable.",
                "scanner_unavailable",
                elapsed_ms,
            )
        provider.record_fallback_metric("model_armor", f"{direction}_error")
        result = (
            self._fallback.scan_input(text)
            if direction == "input"
            else self._fallback.scan_output(text)
        )
        return _to_decision(result, text, elapsed_ms)

    def _evaluate(self, text: str, *, direction: str) -> models.DecisionResult:
        tracer = trace.get_tracer("agentops_sdk.security")
        tracker = provider.get_meter_tracker()
        start_time = time.perf_counter()
        with tracer.start_as_current_span("check_content_safety") as span:
            try:
                result = (
                    self._scanner.scan_input(text)
                    if direction == "input"
                    else self._scanner.scan_output(text)
                )
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                decision = _to_decision(result, text, elapsed_ms)
            except Exception:  # noqa: BLE001
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                decision = self._on_error(text, direction, elapsed_ms)
            span.set_attribute("safety.is_safe", decision.allowed)
            span.set_attribute("safety.verdict", decision.verdict.value)
            attrs = {"direction": direction, "verdict": decision.verdict.value}
            tracker.add_counter("agentops.security.checks.total", 1, attrs)
            if not decision.allowed:
                category = decision.violation_category or ""
                tracker.add_counter(
                    "agentops.security.violations.total",
                    1,
                    {**attrs, "category": category},
                )
            tracker.record_histogram(
                "agentops.security.check.latency", decision.latency_ms, attrs
            )
            return decision

    def check_input(self, text: str) -> models.DecisionResult:
        """Evaluates an inbound user prompt or tool input."""
        return self._evaluate(text, direction="input")

    def check_output(self, text: str) -> models.DecisionResult:
        """Evaluates and masks an outbound model response."""
        return self._evaluate(text, direction="output")


def check_input(
    text: str,
    *,
    scanner: ports.security.SafetyScannerPort | None = None,
    fail_closed: bool | None = None,
) -> models.DecisionResult:
    """Evaluates an inbound prompt via SecurityGuard."""
    guard = SecurityGuard(scanner=scanner, fail_closed=fail_closed)
    return guard.check_input(text)


def check_output(
    text: str,
    *,
    scanner: ports.security.SafetyScannerPort | None = None,
    fail_closed: bool | None = None,
) -> models.DecisionResult:
    """Evaluates an outbound response via SecurityGuard."""
    guard = SecurityGuard(scanner=scanner, fail_closed=fail_closed)
    return guard.check_output(text)
