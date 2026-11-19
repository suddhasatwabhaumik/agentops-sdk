# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Versioned prompt management adapters and sandboxed PromptRegistry."""

from __future__ import annotations

from agentops_sdk.ports import prompts as prompt_ports
from agentops_sdk.prompts import gcs_adapter
from agentops_sdk.prompts import in_memory
from agentops_sdk.prompts import local_yaml_adapter
from agentops_sdk.prompts import registry
from agentops_sdk.prompts import vertex_adapter

GCSPromptAdapter = gcs_adapter.GCSPromptAdapter
InMemoryPromptAdapter = in_memory.InMemoryPromptAdapter
LocalYAMLPromptAdapter = local_yaml_adapter.LocalYAMLPromptAdapter
PromptBackendPort = prompt_ports.PromptBackendPort
PromptRegistry = registry.PromptRegistry
PromptTemplate = prompt_ports.PromptTemplate
VertexAIPromptAdapter = vertex_adapter.VertexAIPromptAdapter
sort_versions = local_yaml_adapter.sort_versions
validate_prompt_id = local_yaml_adapter.validate_prompt_id
validate_version_id = local_yaml_adapter.validate_version_id

__all__ = [
    "GCSPromptAdapter",
    "InMemoryPromptAdapter",
    "LocalYAMLPromptAdapter",
    "PromptBackendPort",
    "PromptRegistry",
    "PromptTemplate",
    "VertexAIPromptAdapter",
    "gcs_adapter",
    "in_memory",
    "local_yaml_adapter",
    "registry",
    "sort_versions",
    "validate_prompt_id",
    "validate_version_id",
    "vertex_adapter",
]
