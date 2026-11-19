# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Conversational session and long-term memory adapters."""

from __future__ import annotations

from typing import TypeAlias

from agentops_sdk.domain import validation
from agentops_sdk.memory import cloud_sql
from agentops_sdk.memory import composite
from agentops_sdk.memory import in_memory
from agentops_sdk.memory import vertex_bank

CloudSQLSessionMemory: TypeAlias = cloud_sql.CloudSQLSessionMemory
CompositeMemory: TypeAlias = composite.CompositeMemory
InMemoryLongTermMemory: TypeAlias = in_memory.InMemoryLongTermMemory
InMemorySessionMemory: TypeAlias = in_memory.InMemorySessionMemory
VertexMemoryBank: TypeAlias = vertex_bank.VertexMemoryBank
validate_user_id = validation.validate_user_id

__all__ = [
    "CloudSQLSessionMemory",
    "CompositeMemory",
    "InMemoryLongTermMemory",
    "InMemorySessionMemory",
    "VertexMemoryBank",
    "cloud_sql",
    "composite",
    "in_memory",
    "validate_user_id",
    "vertex_bank",
]
