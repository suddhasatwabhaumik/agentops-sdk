# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Vertex AI Feature Online Store v2 data-plane, admin, and memory adapters."""

from __future__ import annotations

from agentops_sdk.feature_store import admin
from agentops_sdk.feature_store import in_memory
from agentops_sdk.feature_store import online_client
from agentops_sdk.ports import feature_store as fs_ports

EntityKey = fs_ports.EntityKey
FeatureOnlineStorePort = fs_ports.FeatureOnlineStorePort
FeatureStoreAdminClient = admin.FeatureStoreAdminClient
FeatureStoreOnlineClient = online_client.FeatureStoreOnlineClient
InMemoryFeatureOnlineStore = in_memory.InMemoryFeatureOnlineStore
extract_feature_value = online_client.extract_feature_value
normalize_entity_key = in_memory.normalize_entity_key

__all__ = [
    "EntityKey",
    "FeatureOnlineStorePort",
    "FeatureStoreAdminClient",
    "FeatureStoreOnlineClient",
    "InMemoryFeatureOnlineStore",
    "admin",
    "extract_feature_value",
    "in_memory",
    "normalize_entity_key",
    "online_client",
]
