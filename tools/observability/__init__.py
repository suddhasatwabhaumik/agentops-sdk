# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Observability Log Router administration and telemetry export utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tools.observability.log_router_admin import LogRouterManager

__all__ = ["LogRouterManager"]


def __getattr__(name: str) -> Any:
    if name == "LogRouterManager":
        from tools.observability import log_router_admin

        return log_router_admin.LogRouterManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
