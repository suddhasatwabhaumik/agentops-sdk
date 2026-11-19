# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Shared bounded Jinja2 SandboxedEnvironment templating utilities."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

import jinja2.exceptions
import jinja2.runtime
import jinja2.sandbox

DEFAULT_MAX_TEMPLATE_CHARS = 256_000
_MAX_RANGE_ITEMS = 1000
_MAX_EXPONENT = 64
_SINGLE_BRACE_RE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")
_SEQ_TYPES = (str, bytes, bytearray, list, tuple)


def _bounded_range(*args: int) -> range:
    rng = range(*args)
    if len(rng) > _MAX_RANGE_ITEMS:
        raise OverflowError(
            f"range length {len(rng)} exceeds cap {_MAX_RANGE_ITEMS}."
        )
    return rng


class _BoundedSandboxEnvironment(jinja2.sandbox.SandboxedEnvironment):
    """SandboxedEnvironment capping range, sequence mul, and exponents."""

    intercepted_binops = frozenset({"*", "**"})

    def __init__(self, max_chars: int = DEFAULT_MAX_TEMPLATE_CHARS) -> None:
        super().__init__(autoescape=False, keep_trailing_newline=True)
        self._max_chars = max_chars
        self.globals = {"range": _bounded_range, "dict": dict}

    def _check_mul_bounds(self, left: Any, right: Any) -> None:
        seq: Any = None
        count: Any = None
        if isinstance(left, _SEQ_TYPES) and isinstance(right, int):
            seq, count = left, right
        elif isinstance(right, _SEQ_TYPES) and isinstance(left, int):
            seq, count = right, left
        if seq is None or count <= 0:
            return
        if count > self._max_chars or len(seq) * count > self._max_chars:
            raise jinja2.exceptions.SecurityError(
                "Sequence multiplication exceeds max size."
            )

    def _check_pow_bounds(self, left: Any, right: Any) -> None:
        if not isinstance(left, int | float) or not isinstance(
            right, int | float
        ):
            return
        if abs(right) > _MAX_EXPONENT or (abs(left) > 100 and abs(right) > 10):
            raise OverflowError("Exponentiation exceeds safety bounds.")

    def call_binop(
        self,
        context: jinja2.runtime.Context,
        operator: str,
        left: Any,
        right: Any,
    ) -> Any:
        """Intercepts * and ** operators to prevent CPU/OOM DoS."""
        del context
        if operator == "*":
            self._check_mul_bounds(left, right)
        elif operator == "**":
            self._check_pow_bounds(left, right)
        return self.binop_table[operator](left, right)


_DEFAULT_ENV = _BoundedSandboxEnvironment(DEFAULT_MAX_TEMPLATE_CHARS)


def normalize_template_tokens(template_str: str) -> str:
    """Converts single-brace {var} placeholders to Jinja2 {{ var }} syntax."""
    parts: list[str] = []
    in_raw = False
    for _, kind, value in _DEFAULT_ENV.lex(template_str):
        if kind in {"raw_begin", "raw_end"}:
            in_raw = kind == "raw_begin"
            parts.append(value)
        elif kind == "data" and not in_raw:
            parts.append(_SINGLE_BRACE_RE.sub(r"{{ \1 }}", value))
        else:
            parts.append(value)
    return "".join(parts)


def render_sandboxed_template(
    template_str: str,
    variables: Mapping[str, Any] | None = None,
    *,
    max_chars: int = DEFAULT_MAX_TEMPLATE_CHARS,
) -> str:
    """Renders a template in a size- and resource-bounded Jinja2 sandbox."""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive.")
    if len(template_str) > max_chars:
        raise ValueError("Template exceeds max size.")
    env = (
        _DEFAULT_ENV
        if max_chars == DEFAULT_MAX_TEMPLATE_CHARS
        else _BoundedSandboxEnvironment(max_chars)
    )
    try:
        compiled = env.from_string(normalize_template_tokens(template_str))
        chunks: list[str] = []
        total = 0
        for chunk in compiled.generate(dict(variables or {})):
            total += len(chunk)
            if total > max_chars:
                raise ValueError("Rendered prompt exceeds max size.")
            chunks.append(chunk)
        return "".join(chunks)
    except (
        jinja2.exceptions.SecurityError,
        jinja2.exceptions.TemplateError,
        OverflowError,
        ValueError,
    ) as exc:
        if isinstance(exc, ValueError) and "max size" in str(exc):
            raise
        raise ValueError(f"Render failed: {exc}") from exc
