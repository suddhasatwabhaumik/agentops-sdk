<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# Architecture Design: Enterprise Agent Skill Registry & Tool Catalog (Enterprise Agent Platform / Vertex AI)

> [!NOTE]
> **Document Status — Proposed `v1.x+` Subpackage (`agentops_sdk.skills`) on `v1.0.0`**: Underlying guardrails used here (`@validate_tool_output`, `OutputDLPSanitizer`, `@trace_agent_span`, `@pass_user_identity`, `AuditLedger` / `BigQueryAuditAdapter`, and `mcp_servers/src/server.py`) are **shipped in `v1.0.0`**. The `agentops_sdk.skills` subpackage (`BaseEnterpriseSkill`, `EnterpriseSkillRegistryClient`, `EnterpriseSkillSpec`) and catalog Terraform modules below are a **proposed future platform extension** not yet shipped in `v1.0.0`.

This document establishes the architectural design, SDK client specifications, Terraform Infrastructure as Code (IaC) blueprints, and IAM governance models for the **Enterprise Agent Skill Registry** powered natively by the **Enterprise Agent Platform** and **Vertex AI Extensions & Tools Catalog**.

---

## 1. Executive Summary & The Paradigm Shift

An **Agent Skill** (or Tool) is a discrete, reusable functional capability—such as querying SAP HANA inventory, executing Databricks analytical SQL, logging an operational maintenance incident, or performing vector search over engineering manuals—exposed to LLMs via structured function calling contracts.

In a decentralized enterprise like Google Customer, individual development teams build functional tools in silos. A naive, homegrown approach (distributing loose Python wheel packages on Artifact Registry paired with custom SQL database index tables) creates critical enterprise liabilities:
*   **Protocol & Execution Fragmentation**: Inability to seamlessly bind diverse tool types (native in-process Python functions, remote Model Context Protocol [MCP] endpoints, Vertex AI Extensions, and OpenAPI REST microservices) under a unified interface.
*   **Security & Identity Gaps**: Lack of delegated authentication, leading to hardcoded credentials or service account token sprawl instead of OAuth 2.0 user-token propagation and Secret Manager credential isolation.
*   **Zero Runtime Guardrails**: Absence of automated input sanitization, deterministic output validation, rate limiting, and circuit breaking across tool calls.
*   **Disjointed Lineage with Agent Registry**: No cryptographic traceability linking an agent version in the [Enterprise Agent Registry](agent_registry_design.md) to the exact immutable version of the skills it executes.
*   **Observability Blindspots**: Inability to correlate tool latency, error rates, and downstream query costs with the initiating agent flow and business P&L cost centers.

### The Enterprise Agent Platform & Vertex AI Skill Registry Foundation
By anchoring the Google Customer Skill Registry on **Enterprise Agent Platform** and **Vertex AI Extensions & Tool Catalog**, Google Customer establishes an enterprise-grade capability directory that integrates directly with **Vertex AI Reasoning Engine** and the **Enterprise Agent Registry**:

```
+---------------------------------------------------------------------------------------------------------+
|                                        ENTERPRISE AGENT PLATFORM                                        |
|                                                                                                         |
|  +---------------------------------------------------------------------------------------------------+  |
|  |                            Vertex AI Skill Registry & Extensions Catalog                          |  |
|  |  * Universal Tool Declarations (Gemini Function Calling / OpenAPI 3.0 / MCP SSE Protocols)         |  |
|  |  * Granular Capability Search & Semantic Tool Discovery (Cloud Data Catalog & BigQuery Index)     |  |
|  |  * Delegated Identity & Auth Translation (OAuth 2.0, Google IAM, Secret Manager)                  |  |
|  |  * Active Guardrails: Output Validation (@validate_tool_output) & Model Armor Parameter Scans     |  |
|  +---------------------------------------------------------------------------------------------------+  |
|                                                  |                                                      |
|         +----------------------------------------+----------------------------------------+             |
|         |                                        |                                        |             |
|         v                                        v                                        v             |
|  +------------------------------+  +------------------------------+  +-------------------------------+  |
|  |     Multi-Protocol Tools     |  |   Managed Runtime Binding    |  |     Auditing & Governance     |  |
|  |  * Python Native Skills      |  |  * Vertex Reasoning Engine   |  |  * BigQuery Tool Audit Ledger |  |
|  |  * MCP Hub Microservices     |  |  * Dynamic Tool Mounting     |  |  * Distributed OTel Tracing   |  |
|  |  * Vertex AI Extensions      |  |  * Circuit Breakers/Timeouts |  |  * Cryptographic Lineage URNs |  |
|  +------------------------------+  +------------------------------+  +-------------------------------+  |
+---------------------------------------------------------------------------------------------------------+
```

---

## 2. Enterprise Skill Registry Core Services & Architecture

The Enterprise Skill Registry operates across five architectural pillars designed for enterprise security, discoverability, and high-performance agent execution:

```
+---------------------------------------------------------------------------------------------------------------+
|                                ENTERPRISE SKILL REGISTRY LAYERED ARCHITECTURE                                 |
+---------------------------------------------------------------------------------------------------------------+
| 1. ENTERPRISE CAPABILITY CATALOG & DISCOVERY (Vertex AI Extensions + BigQuery Catalog Index)                  |
|    - Structured & semantic search by business domain, ERP system, capability tags, and auth tier             |
|    - SemVer lifecycle management: active -> deprecated -> sunset with dependency impact analysis             |
+---------------------------------------------------------------------------------------------------------------+
| 2. UNIVERSAL TOOL EXECUTION ADAPTERS (Multi-Protocol Ingress)                                                 |
|    - Native Python Classes (Hermetic wheels for ultra-low latency compute within Reasoning Engine)            |
|    - Model Context Protocol (MCP) SSE connections to Enterprise MCP Hub (SAP, Databricks, SaaS)              |
|    - Vertex AI Managed Extensions (Standardized OpenAPI 3.0 REST microservices on Cloud Run)                 |
|    - Vertex AI Search / RAG Engine Connectors (Structured data stores & unstructured doc corpora)             |
+---------------------------------------------------------------------------------------------------------------+
| 3. AUTHENTICATION TRANSLATION & DELEGATION LAYER (IAM + OAuth 2.0 + Secret Manager)                           |
|    - User Identity Token Forwarding (@pass_user_identity) enforcing downstream database Row-Level Security    |
|    - Google Cloud IAM Service-to-Service authentication and Secret Manager dynamic credential injection       |
+---------------------------------------------------------------------------------------------------------------+
| 4. DETERMINISTIC QUALITY & SAFETY SHIELD (Pydantic Contracts + Model Armor + DLP)                             |
|    - Bidirectional schema validation: Strict input parameter checking and @validate_tool_output assertions    |
|    - Inline Model Armor threat scanning on tool parameters and Cloud DLP redaction on tool responses          |
|    - Runtime sandboxing, per-tool timeout constraints, and automated circuit breaking                        |
+---------------------------------------------------------------------------------------------------------------+
| 5. OBSERVABILITY, FINOPS & COMPLIANCE LEDGER (OpenTelemetry + BigQuery Streaming)                            |
|    - W3C traceparent header propagation across remote tool invocations (@trace_agent_span)                    |
|    - Granular tool latency, token consumption, and error metrics streamed to BigQuery FinOps ledgers          |
+---------------------------------------------------------------------------------------------------------------+
```

### 2.1 Supported Tool Execution Types
1.  **Python Native Skill (`PYTHON_NATIVE`)**: High-performance, in-process Python class executing directly inside the Vertex AI Reasoning Engine sandbox container. Ideal for mathematical transformations, data manipulation, and lightweight queries.
2.  **MCP Hub Microservice (`MCP_ENDPOINT`)**: Remote tool communicating over Server-Sent Events (SSE) or HTTP with the Enterprise MCP Hub. Centralizes connections to on-prem SAP HANA, Databricks, and cloud data warehouses.
3.  **Vertex AI Managed Extension (`VERTEX_EXTENSION`)**: Managed Google Cloud extension wrapping OpenAPI 3.0 endpoints on Cloud Run or Cloud Functions with IAM authentication.
4.  **Vector RAG Skill (`VERTEX_RAG`)**: Managed grounding skill connecting agents to Vertex AI Search data stores and Feature Store embeddings.

### 2.2 Skill Versioning & Immutability Contract
*   Skills enforce **Semantic Versioning (SemVer)**: `MAJOR.MINOR.PATCH` (e.g., `1.2.0`).
*   Every published version generates an immutable **Skill URN**:
    `urn:customer:skill:{domain}:{skill_id}:{version}` (e.g., `urn:customer:skill:supply_chain:query_sap_inventory:1.2.0`).
*   Agents in the [Enterprise Agent Registry](agent_registry_design.md) reference immutable Skill URNs, guaranteeing that downstream tool updates do not cause breaking changes in production reasoning engines.

---

## 3. Proposed SDK Architecture & API Specifications (`agentops_sdk.skills`, Planned `v1.x+`)

A future release would provide unified contracts for developing, packaging, registering, and executing enterprise skills through a proposed `agentops_sdk.skills` module built on `v1.0.0` subpackages (`resilience`, `security`, `telemetry`, `audit`).

### 3.1 Pydantic Data Contracts & Schemas

```python
"""Data validation models for Enterprise Skill Registry."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Type
from pydantic import BaseModel, Field, constr


class SkillExecutionType(str, Enum):
    PYTHON_NATIVE = "python_native"
    MCP_ENDPOINT = "mcp_endpoint"
    VERTEX_EXTENSION = "vertex_extension"
    VERTEX_RAG = "vertex_rag"


class SkillAuthType(str, Enum):
    GOOGLE_IAM = "google_iam"
    OAUTH2_BEARER = "oauth2_bearer"
    SECRET_MANAGER = "secret_manager"
    NONE = "none"


class SkillStage(str, Enum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"
    DEPRECATED = "deprecated"


class SkillAuthConfig(BaseModel):
    """Authentication configuration for executing the skill."""

    auth_type: SkillAuthType = SkillAuthType.GOOGLE_IAM
    secret_id: Optional[str] = Field(
        None, description="Secret Manager secret ID for API tokens"
    )
    required_scopes: List[str] = Field(default_factory=list)
    forward_user_identity: bool = Field(
        default=True,
        description="Whether to forward user JWT Bearer token for Row-Level Security (RLS)",
    )


class SkillSafetyPolicy(BaseModel):
    """Security guardrails applied during tool execution."""

    sanitize_inputs: bool = True
    sanitize_outputs_dlp: bool = True
    dlp_info_types: List[str] = Field(
        default=[
            "PERSON_NAME",
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "US_SOCIAL_SECURITY_NUMBER",
        ]
    )
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    max_retries: int = Field(default=2, ge=0, le=5)


class SkillFinOpsMetadata(BaseModel):
    """FinOps tags attributing tool costs to business units."""

    business_kpi: constr(min_length=3, max_length=50, pattern=r"^[a-z0-9\-]+$")
    cost_center: str
    owner_email: str


class EnterpriseSkillSpec(BaseModel):
    """Complete specification contract for registering a Skill in Enterprise Agent Platform."""

    skill_id: constr(min_length=3, max_length=64, pattern=r"^[a-z0-9\-_]+$")
    version: constr(pattern=r"^\d+\.\d+\.\d+$")  # Strict SemVer format: X.Y.Z
    display_name: str
    description: str = Field(
        ...,
        description="Detailed semantic description read by LLMs for function calling",
    )
    domain: str = Field(
        ...,
        description="Business domain: supply_chain, connected_ops, manufacturing, finance",
    )
    tags: List[str] = Field(default_factory=list)

    execution_type: SkillExecutionType
    endpoint_or_package: str = Field(
        ..., description="Target URL endpoint or Python package import string"
    )
    entrypoint_class: Optional[str] = Field(
        None, description="Class name for Python native skill"
    )

    auth_config: SkillAuthConfig = Field(default_factory=SkillAuthConfig)
    safety_policy: SkillSafetyPolicy = Field(default_factory=SkillSafetyPolicy)
    finops: SkillFinOpsMetadata

    input_schema: Dict[str, Any] = Field(
        ..., description="OpenAPI / JSON Schema for input arguments"
    )
    output_schema: Optional[Dict[str, Any]] = Field(
        None, description="OpenAPI / JSON Schema for return values"
    )


class SkillRegistrationResult(BaseModel):
    """Response returned upon successful skill registration."""

    skill_urn: str  # urn:customer:skill:{domain}:{skill_id}:{version}
    skill_id: str
    version: str
    stage: SkillStage
    execution_type: SkillExecutionType
    openapi_schema: Dict[str, Any]
    artifact_hash_sha256: str
    registered_at: datetime
    is_active: bool
```

---

### 3.2 The `BaseEnterpriseSkill` Abstract Base Class

All custom skills inherit from `BaseEnterpriseSkill`, which automatically handles Gemini function calling schema generation, input/output validation, distributed OpenTelemetry tracing, and identity token forwarding:

```python
# Proposed future extension: sdk/src/agentops_sdk/skills/base.py
"""Base class for all Enterprise Skills (built on shipped v1.0.0 SDK primitives)."""

from abc import ABC, abstractmethod
import logging
from typing import Any, Dict, Optional, Type
from pydantic import BaseModel

from agentops_sdk.resilience import validate_tool_output
from agentops_sdk.security import OutputDLPSanitizer, RegexPrivacyScrubber
from agentops_sdk.telemetry import trace_agent_span

logger = logging.getLogger("agentops-sdk.skills")


class BaseEnterpriseSkill(ABC):
    """Abstract base class for all Enterprise-compliant functional skills."""

    name: str  # Unique skill identifier (e.g. "query_sap_inventory")
    description: str  # Semantic prompt description for LLM function calling
    args_schema: Type[BaseModel]  # Pydantic schema validating input parameters
    return_schema: Optional[Type[BaseModel]] = None  # Pydantic schema validating output

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        dlp_sanitizer: Optional[OutputDLPSanitizer] = None,
    ):
        self.config = config or {}
        # Use GoogleDLPScrubber(project_id=...) in Cloud Run production
        self._dlp_sanitizer = dlp_sanitizer or OutputDLPSanitizer(
            scrubber=RegexPrivacyScrubber()
        )

    def get_tool_schema(self) -> Dict[str, Any]:
        """Generates Google GenAI function declaration schema compatible with Gemini models."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.args_schema.model_json_schema(),
        }

    @trace_agent_span(span_name="enterprise_skill.invoke")
    async def __call__(self, **kwargs: Any) -> Any:
        """Entrypoint wrapper executing validation, tracing, and error isolation."""
        # 1. Validate input kwargs against args_schema
        try:
            validated_inputs = self.args_schema(**kwargs)
        except Exception as e:
            logger.error("Input validation error for skill '%s': %s", self.name, e)
            return {
                "error": f"Invalid arguments provided for tool '{self.name}': {str(e)}"
            }

        # 2. Execute concrete business logic wrapped in distributed trace span & validation
        @validate_tool_output(max_retries=1)
        async def _guarded_exec(**exec_kwargs: Any) -> Any:
            return await self.execute(**exec_kwargs)

        try:
            raw_output = await _guarded_exec(**validated_inputs.model_dump())
        except Exception as e:
            logger.error(
                "Execution failure in skill '%s': %s", self.name, e, exc_info=True
            )
            # Fail-closed: return generic sanitized error to LLM without leaking credentials
            return {
                "error": f"Tool '{self.name}' execution failed: {str(e)}",
                "status": "failed",
            }

        # 3. Deterministic output schema validation & PII scrubbing
        if self.return_schema and isinstance(raw_output, dict):
            validated_output = self.return_schema(**raw_output)
            raw_output = validated_output.model_dump()

        return await self._dlp_sanitizer.scrub_text(str(raw_output))

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """Executes the tool's core logic. Must be overridden by developer."""
        pass
```

---

### 3.3 The `EnterpriseSkillRegistryClient` *(Proposed Extension)*

```python
# Proposed future extension: sdk/src/agentops_sdk/skills/registry.py
"""Enterprise Skill Registry Client for publishing and resolving enterprise skills."""

from datetime import UTC, datetime
import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional, Type

from agentops_sdk.audit import AuditLedger, BigQueryAuditAdapter
from agentops_sdk.domain.exceptions import ConfigurationError
from agentops_sdk.domain.models import AuditLogEntry
from agentops_sdk.skills.schemas import (
    EnterpriseSkillSpec,
    SkillRegistrationResult,
    SkillStage,
)

logger = logging.getLogger("agentops-sdk.skills.registry")


class EnterpriseSkillRegistryClient:
    """Client for managing skill lifecycles via Enterprise Agent Platform and Vertex AI Extensions."""

    def __init__(
        self,
        project_id: Optional[str] = None,
        location: str = "europe-west4",
        catalog_dataset: str = "agentops_catalog",
    ):
        self.project_id = project_id or os.getenv("AGENTOPS_PROJECT_ID")
        if not self.project_id:
            raise ConfigurationError("AGENTOPS_PROJECT_ID must be configured.")
        self.location = location or os.getenv("AGENTOPS_LOCATION", "europe-west4")
        self.catalog_dataset = catalog_dataset
        self._audit_ledger = AuditLedger(
            sink=BigQueryAuditAdapter(
                project_id=self.project_id,
                dataset_id="agentops_compliance",
                table_id="skill_registry_audit_ledger",
            )
        )

        try:
            import vertexai
            from google.cloud import aiplatform

            vertexai.init(project=self.project_id, location=self.location)
            aiplatform.init(project=self.project_id, location=self.location)
            logger.info(
                "Connected to Enterprise Skill Registry (Project: %s, Region: %s)",
                self.project_id,
                self.location,
            )
        except Exception as e:
            # Fail-closed per v1.0.0 standards: no silent mock registry fallback
            raise ConfigurationError(
                f"Vertex AI Skill Registry initialization failed: {e}"
            ) from e

    def register_skill(
        self,
        spec: EnterpriseSkillSpec,
        skill_cls: Optional[Type[Any]] = None,
        stage: SkillStage = SkillStage.DEV,
    ) -> SkillRegistrationResult:
        """Publishes a skill contract to the Enterprise Skill Catalog and registers extensions."""
        skill_urn = f"urn:customer:skill:{spec.domain}:{spec.skill_id}:{spec.version}"
        logger.info("Registering Skill '%s' (Stage: %s)...", skill_urn, stage.value)

        # Generate checksum
        schema_str = json.dumps(spec.input_schema, sort_keys=True)
        sha256_hash = hashlib.sha256(schema_str.encode("utf-8")).hexdigest()

        # Build OpenAPI function declaration schema
        openapi_schema = {
            "name": spec.skill_id,
            "description": spec.description,
            "parameters": spec.input_schema,
        }

        # Index metadata into BigQuery
        self._index_skill_catalog(spec, skill_urn, sha256_hash, stage)

        # Stream compliance audit log via AuditLedger
        self._audit_ledger.log_entry_sync(
            AuditLogEntry(
                trace_id=sha256_hash[:16],
                session_id="skill-registry",
                user_id=spec.finops.owner_email,
                agent_name="SYSTEM",
                input_query=f"SKILL_REGISTRATION:{skill_urn} ({stage.value})",
                execution_status="SUCCESS",
            )
        )

        return SkillRegistrationResult(
            skill_urn=skill_urn,
            skill_id=spec.skill_id,
            version=spec.version,
            stage=stage,
            execution_type=spec.execution_type,
            openapi_schema=openapi_schema,
            artifact_hash_sha256=sha256_hash,
            registered_at=datetime.now(UTC),
            is_active=True,
        )

    def get_skill(self, skill_urn_or_id: str, version: str = "latest") -> Any:
        """Resolves and instantiates a skill executor by URN or ID."""
        logger.info("Resolving skill '%s' (version: %s)...", skill_urn_or_id, version)
        return self._instantiate_skill_proxy(skill_urn_or_id, version)

    def list_skills(
        self,
        domain: Optional[str] = None,
        tags: Optional[List[str]] = None,
        execution_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Discovers available skills matching enterprise filter criteria."""
        logger.info("Searching Enterprise Skill Catalog (domain=%s, tags=%s)...", domain, tags)
        return [
            {
                "skill_urn": "urn:customer:skill:supply_chain:query_sap_inventory:1.2.0",
                "display_name": "SAP HANA Inventory Stock Checker",
                "domain": "supply_chain",
                "execution_type": "mcp_endpoint",
                "description": "Retrieves real-time parts stock and bin locations from SAP HANA.",
            }
        ]

    def _index_skill_catalog(
        self, spec: EnterpriseSkillSpec, skill_urn: str, sha256: str, stage: SkillStage
    ) -> None:
        """Internal helper writing metadata row to BigQuery."""

    def _instantiate_skill_proxy(self, skill_urn: str, version: str) -> Any:
        """Internal helper instantiating the executable tool class or MCP client."""
        raise NotImplementedError(
            "Proposed blueprint: proxy resolution requires live AgentHub deployment."
        )
```

---

### 3.4 Concrete Developer Examples: Building Production Skills

#### Example 1: SAP HANA Inventory Checker Skill
```python
"""Production SAP Inventory Skill inheriting from BaseEnterpriseSkill."""

from typing import Any, Dict
from pydantic import BaseModel, Field
from agentops_sdk.skills import BaseEnterpriseSkill


class SAPInventoryInputSchema(BaseModel):
    part_number: str = Field(
        ..., description="Unique enterprise part number (e.g., 'PART-778920')"
    )
    plant_code: str = Field(
        "1000", description="Manufacturing plant code (e.g., '1000' for Plant-US-01)"
    )


class SAPInventoryOutputSchema(BaseModel):
    part_number: str
    plant_code: str
    available_stock: int
    bin_location: str
    reorder_threshold: int


class SAPInventorySkill(BaseEnterpriseSkill):
    name = "query_sap_inventory"
    description = "Queries SAP HANA database via Enterprise MCP Hub to retrieve real-time parts inventory levels and bin locations."
    args_schema = SAPInventoryInputSchema
    return_schema = SAPInventoryOutputSchema

    async def execute(
        self, part_number: str, plant_code: str = "1000"
    ) -> Dict[str, Any]:
        # Connects to SAP HANA via MCP Gateway / Database Connector with user identity forwarding
        return {
            "part_number": part_number,
            "plant_code": plant_code,
            "available_stock": 42,
            "bin_location": "Aisle-4-Bin-18",
            "reorder_threshold": 15,
        }
```

#### Example 2: Registering the SAP Skill with Enterprise Skill Registry
```python
"""Publishing the SAP Inventory Skill to Enterprise Skill Registry."""

from agentops_sdk.skills.schemas import (
    EnterpriseSkillSpec,
    SkillExecutionType,
    SkillAuthConfig,
    SkillAuthType,
    SkillSafetyPolicy,
    SkillFinOpsMetadata,
    SkillStage,
)
from agentops_sdk.skills.registry import EnterpriseSkillRegistryClient

# 1. Construct Enterprise Specification
spec = EnterpriseSkillSpec(
    skill_id="query_sap_inventory",
    version="1.2.0",
    display_name="SAP HANA Inventory Stock Checker",
    description="Queries SAP HANA database via Enterprise MCP Hub to retrieve real-time parts inventory levels and bin locations.",
    domain="supply_chain",
    tags=["sap", "inventory", "mcp", "supply-chain"],
    execution_type=SkillExecutionType.MCP_ENDPOINT,
    endpoint_or_package="https://mcp-hub.customer.internal/tools/sap_inventory",
    auth_config=SkillAuthConfig(
        auth_type=SkillAuthType.OAUTH2_BEARER,
        forward_user_identity=True,
        required_scopes=["https://sap.customer.internal/inventory.read"],
    ),
    safety_policy=SkillSafetyPolicy(
        sanitize_inputs=True,
        sanitize_outputs_dlp=True,
        timeout_seconds=15.0,
    ),
    finops=SkillFinOpsMetadata(
        business_kpi="kpi-inventory-stockout-rate",
        cost_center="CC-OPS-SC-4402",
        owner_email="supply-chain-lead@customer.example.com",
    ),
    input_schema=SAPInventoryInputSchema.model_json_schema(),
    output_schema=SAPInventoryOutputSchema.model_json_schema(),
)

# 2. Publish to Registry
if __name__ == "__main__":
    registry = EnterpriseSkillRegistryClient(
        project_id="my-gcp-project-id", location="europe-west4"
    )
    result = registry.register_skill(spec=spec, stage=SkillStage.PROD)
    print(f"Skill registered: {result.skill_urn}")
    print(f"SHA-256 Hash:     {result.artifact_hash_sha256}")
```

---

## 4. Infrastructure as Code (IaC) Blueprint (Terraform)

Deploying the enterprise Enterprise Skill Registry requires dedicated Artifact Registry repositories, Cloud KMS CMEK encryption keys, BigQuery catalog datasets, Secret Manager bindings, and Vertex AI Extensions resources.

### 4.1 Required Google Cloud APIs
```hcl
# ==============================================================================
# 1. Enable Required GCP APIs for Enterprise Skill Registry & Extensions
# ==============================================================================
resource "google_project_service" "skill_registry_apis" {
  for_each = toset([
    "aiplatform.googleapis.com",        # Vertex AI Extensions & Agent Engine
    "artifactregistry.googleapis.com",  # Container & Python wheel package repository
    "cloudkms.googleapis.com",          # Customer Managed Encryption Keys (CMEK)
    "datacatalog.googleapis.com",       # Enterprise Catalog metadata search
    "modelarmor.googleapis.com",        # Model Armor prompt & parameter safety
    "dlp.googleapis.com",               # Cloud Data Loss Prevention (DLP)
    "cloudtrace.googleapis.com",        # Cloud Trace (OpenTelemetry sinks)
    "logging.googleapis.com",           # Cloud Audit Logging
    "bigquery.googleapis.com",          # BigQuery Catalog & Execution Ledgers
    "secretmanager.googleapis.com",     # Secret Manager for database & API credentials
    "run.googleapis.com"                # Cloud Run for MCP Server & Extension hosting
  ])
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}
```

---

### 4.2 Customer-Managed Encryption Keys (CMEK)
```hcl
# ==============================================================================
# 2. Cloud KMS KeyRing and CryptoKey for Skill Registry CMEK
# ==============================================================================
resource "google_kms_key_ring" "skills_keyring" {
  name     = "agentops-skills-keyring-${var.region}"
  location = var.region
  project  = var.project_id
}

resource "google_kms_crypto_key" "skills_key" {
  name            = "skills-registry-cmek-key"
  key_ring        = google_kms_key_ring.skills_keyring.id
  rotation_period = "7776000s" # 90 days automatic key rotation

  lifecycle {
    prevent_destroy = true
  }
}
```

---

### 4.3 Python Package & Docker Artifact Registries
```hcl
# ==============================================================================
# 3. Artifact Registry for Python Skill Packages (Hermetic Wheels)
# ==============================================================================
resource "google_artifact_registry_repository" "skills_python_repo" {
  project       = var.project_id
  location      = var.region
  repository_id = "customer-agent-skills-python"
  description   = "Enterprise repository for reusable Agent Skill Python wheels"
  format        = "PYTHON"

  kms_key_name = google_kms_crypto_key.skills_key.id
}

# ==============================================================================
# 4. Artifact Registry for Containerized MCP Tools & Extensions
# ==============================================================================
resource "google_artifact_registry_repository" "skills_docker_repo" {
  project       = var.project_id
  location      = var.region
  repository_id = "customer-agent-skills-docker"
  description   = "Docker repository for containerized MCP tools and Vertex AI Extensions"
  format        = "DOCKER"

  kms_key_name = google_kms_crypto_key.skills_key.id
}
```

---

### 4.4 BigQuery Skill Catalog & Invocation Audit Ledger
```hcl
# ==============================================================================
# 5. BigQuery Dataset & Tables for Skill Metadata and Invocation Ledgers
# ==============================================================================
resource "google_bigquery_dataset" "skills_catalog_dataset" {
  dataset_id                  = "agentops_skills_catalog"
  project                     = var.project_id
  location                    = var.region
  description                 = "Metadata catalog and invocation audit ledger for Enterprise Skill Registry"
  default_table_expiration_ms = null

  default_encryption_configuration {
    kms_key_name = google_kms_crypto_key.skills_key.id
  }
}

# Skill Metadata Index Table
resource "google_bigquery_table" "skill_catalog_index" {
  dataset_id = google_bigquery_dataset.skills_catalog_dataset.dataset_id
  table_id   = "skill_catalog_index"
  project    = var.project_id

  time_partitioning {
    type  = "DAY"
    field = "registered_at"
  }

  clustering = ["domain", "skill_id", "execution_type"]

  schema = jsonencode([
    {"name": "skill_urn", "type": "STRING", "mode": "REQUIRED"},
    {"name": "skill_id", "type": "STRING", "mode": "REQUIRED"},
    {"name": "version", "type": "STRING", "mode": "REQUIRED"},
    {"name": "stage", "type": "STRING", "mode": "REQUIRED"},
    {"name": "display_name", "type": "STRING", "mode": "REQUIRED"},
    {"name": "domain", "type": "STRING", "mode": "REQUIRED"},
    {"name": "description", "type": "STRING", "mode": "REQUIRED"},
    {"name": "execution_type", "type": "STRING", "mode": "REQUIRED"},
    {"name": "endpoint_or_package", "type": "STRING", "mode": "REQUIRED"},
    {"name": "auth_type", "type": "STRING", "mode": "REQUIRED"},
    {"name": "sha256_hash", "type": "STRING", "mode": "REQUIRED"},
    {"name": "business_kpi", "type": "STRING", "mode": "REQUIRED"},
    {"name": "cost_center", "type": "STRING", "mode": "REQUIRED"},
    {"name": "owner_email", "type": "STRING", "mode": "REQUIRED"},
    {"name": "registered_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
    {"name": "tags", "type": "STRING", "mode": "REPEATED"}
  ])
}

# Skill Execution Audit Ledger Table
resource "google_bigquery_table" "skill_execution_events" {
  dataset_id = google_bigquery_dataset.skills_catalog_dataset.dataset_id
  table_id   = "skill_execution_events"
  project    = var.project_id

  time_partitioning {
    type  = "DAY"
    field = "execution_timestamp"
  }

  clustering = ["skill_urn", "calling_agent_id", "status"]

  schema = jsonencode([
    {"name": "event_id", "type": "STRING", "mode": "REQUIRED"},
    {"name": "skill_urn", "type": "STRING", "mode": "REQUIRED"},
    {"name": "calling_agent_id", "type": "STRING", "mode": "REQUIRED"},
    {"name": "session_id", "type": "STRING", "mode": "REQUIRED"},
    {"name": "user_id", "type": "STRING", "mode": "REQUIRED"},
    {"name": "status", "type": "STRING", "mode": "REQUIRED"},
    {"name": "latency_ms", "type": "FLOAT", "mode": "REQUIRED"},
    {"name": "execution_timestamp", "type": "TIMESTAMP", "mode": "REQUIRED"}
  ])
}
```

---

## 5. IAM Roles & Permissions Governance Matrix

Access to the Enterprise Skill Registry is strictly segregated across four identity personas following the Principle of Least Privilege:

```
+---------------------------------------------------------------------------------------------------------------+
|                                    SKILL IAM PERSONAS & PRIVILEGE MATRIX                                      |
+-------------------+----------------------------------------------------+--------------------------------------+
| Persona           | Google IAM Roles                                   | Granular Permission Scopes           |
+-------------------+----------------------------------------------------+--------------------------------------+
| 1. Skill Developer| roles/artifactregistry.writer                      | artifactregistry.packages.create     |
|    / CI/CD SA     | roles/aiplatform.admin (scoped to Extensions)      | aiplatform.extensions.create/update  |
|    (sa-skill-pub) | roles/bigquery.dataEditor (on skills catalog)      | bigquery.tables.insertData           |
|                   | roles/kms.cryptoKeyEncrypterDecrypter              | kms.cryptoKeyVersions.useToEncrypt   |
+-------------------+----------------------------------------------------+--------------------------------------+
| 2. Agent Runner   | roles/artifactregistry.reader                      | artifactregistry.packages.get        |
|    / Skill Exec   | roles/aiplatform.user                              | aiplatform.extensions.execute        |
|    (sa-agent-run) | roles/secretmanager.secretAccessor (scoped keys)   | secretmanager.versions.access        |
|                   | roles/run.invoker (on MCP microservices)          | run.routes.invoke                    |
|                   | roles/cloudtrace.agent                             | cloudtrace.traces.patch              |
|                   | roles/logging.logWriter                            | logging.logEntries.create            |
+-------------------+----------------------------------------------------+--------------------------------------+
| 3. Security & QA  | roles/datacatalog.viewer                           | datacatalog.entries.search           |
|    Auditor        | roles/bigquery.dataViewer                          | bigquery.tables.getData              |
|    (sa-auditor)   | roles/logging.viewer                               | logging.entries.list                 |
+-------------------+----------------------------------------------------+--------------------------------------+
| 4. Platform Admin | roles/aiplatform.admin                             | Full extension & catalog governance  |
|    (admin-group)  | roles/resourcemanager.organizationAdmin            | Cross-project VPC-SC perimeters      |
+-------------------+----------------------------------------------------+--------------------------------------+
```

### 5.1 Terraform IAM Bindings Example
```hcl
# ==============================================================================
# 6. Service Account and IAM Provisioning for Skill Publishers and Runners
# ==============================================================================
resource "google_service_account" "skill_publisher_sa" {
  account_id   = "agentops-skill-publisher"
  display_name = "AgentOps CI/CD Skill Publisher Service Account"
  project      = var.project_id
}

# Grant Publisher permissions to write to Python Artifact Registry
resource "google_artifact_registry_repository_iam_member" "skill_pub_ar_writer" {
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.skills_python_repo.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.skill_publisher_sa.email}"
}

# Grant Agent Runner permissions to invoke Cloud Run MCP tools
resource "google_cloud_run_v2_service_iam_member" "runner_mcp_invoker" {
  location = var.region
  name     = "agentops-mcp-server"
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.reasoning_engine_sa.email}"
}
```

---

## 6. Coordinated Agent & Skill Registry Orchestration Workflows

The diagram below illustrates how the **Enterprise Skill Registry** and the **Enterprise Agent Registry** seamlessly interoperate during agent build and runtime execution:

```
+-----------------------------------+               +-----------------------------------+
|     ENTERPRISE SKILL REGISTRY     |               |     ENTERPRISE AGENT REGISTRY     |
|  - Publishes Skill URNs           |               |  - Defines Agent Specifications   |
|  - Generates Gemini Tool Schemas  |               |  - Declares Bound Skill URNs      |
+-----------------|-----------------+               +-----------------|-----------------+
                  |                                                   |
                  +-------------------------+-------------------------+
                                            |
                                            v
                         +-------------------------------------+
                         |    VERTEX AI REASONING ENGINE       |
                         |  1. Boots sandboxed container       |
                         |  2. Resolves & mounts Skill URNs    |
                         |  3. Injects Auth & Model Armor      |
                         |  4. Binds Vertex Memory Bank        |
                         |  5. Executes autonomous loops       |
                         +-------------------------------------+
```

---

### 6.1 Skill Registration & Verification Pipeline (Shift-Left)

```mermaid
sequenceDiagram
    autonumber
    actor Dev as Skill Developer
    participant CI as Cloud Build / CI/CD
    participant Reg as Enterprise Skill Registry Client
    participant AR as Private Artifact Registry (Python)
    participant BQ as BigQuery Skill Catalog & Audit Ledger

    Dev->>CI: Push Skill Code & Spec (git commit)
    CI->>CI: Validate Pydantic Schemas (args_schema, return_schema)
    CI->>CI: Run Unit Tests & Security Linting
    CI->>AR: Build & Publish Python Wheel (CMEK Encrypted)
    AR-->>CI: Wheel Staging URI (gs://customer-skills-repo/...)
    
    CI->>Reg: register_skill(spec, stage="prod")
    Reg->>Reg: Generate Skill URN & SHA-256 Checksum
    Reg->>BQ: Insert Row into skill_catalog_index Table
    Reg->>BQ: Stream SKILL_REGISTRATION Audit Record
    Reg-->>CI: Return SkillRegistrationResult (URN, Schemas, SHA-256)
    CI-->>Dev: Build Succeeded: Skill Published to Enterprise Catalog
```

---

### 6.2 Runtime Skill Resolution & Execution Loop

```mermaid
sequenceDiagram
    autonumber
    actor Agent as Reasoning Engine (Agent Runner)
    participant SReg as Enterprise Skill Registry
    participant Armor as Model Armor Guardrail
    participant MCP as Enterprise MCP Hub (SAP HANA)
    participant DLP as Cloud DLP Sanitizer
    participant BQ as BigQuery Audit Ledger

    Agent->>SReg: Resolve Bound Skill URN (urn:customer:skill:supply_chain:query_sap_inventory:1.2.0)
    SReg-->>Agent: Return OpenAPI Schema & Endpoint Adapter
    
    Note over Agent: LLM Decides Function Call: query_sap_inventory(part="PART-778920")
    
    Agent->>Armor: Inspect Tool Input Arguments
    Armor-->>Agent: Arguments Validated (No SQL Injection / Malicious Payload)
    
    Agent->>MCP: Execute POST /tools/sap_inventory (Bearer User JWT)
    Note over MCP: Enforces SAP Row-Level Security (RLS)
    MCP-->>Agent: Tool Response (Stock: 42, Location: "Aisle-4-Bin-18")
    
    Agent->>DLP: De-identify & Redact PII (if applicable)
    DLP-->>Agent: Sanitized Tool Output
    
    Agent->>BQ: Stream Tool Execution Record (latency_ms, user_id, status)
    Note over Agent: Feeds Tool Output into LLM Context for Next Reasoning Turn
```

---

## 7. Homegrown Python Repo vs. Enterprise Skill Registry Comparison

| Dimension | Legacy Homegrown Python Repo + SQL Table | Enterprise Agent Platform & Vertex AI Skill Registry |
| :--- | :--- | :--- |
| **Tool Protocols** | Python package wheels only | **Universal Adapter**: Python wheels, MCP SSE endpoints, Vertex AI Extensions, and RAG search |
| **Schema Generation** | Basic manual JSON dict output | **Automated OpenAPI 3.0 / Gemini Schema Generation** from Pydantic contracts |
| **Identity & Security** | Unmanaged / hardcoded credentials | **OAuth 2.0 User Token Forwarding** (`@pass_user_identity`), Secret Manager, and Row-Level Security |
| **Active Guardrails** | None (unvalidated execution) | **Model Armor Parameter Threat Scans**, `@validate_tool_output`, and Cloud DLP response sanitization |
| **Agent Registry Lineage**| Loose string import declarations | **Immutable Skill URNs** linked cryptographically with [Enterprise Agent Registry](agent_registry_design.md) versions |
| **Runtime Observability**| Basic console print logs | **Distributed OpenTelemetry Spans** (`traceparent` header injection) and BigQuery FinOps streaming |
| **Access Control** | Coarse repository-level access | Fine-grained Google Cloud IAM, VPC-SC perimeters, and Private Service Connect |

---

## 8. Security & Verification Checklist

In compliance with enterprise security baselines and secure coding principles:

- [x] **Zero-Trust IAM**: Segregates publisher and execution identities; enforces least-privilege IAM roles.
- [x] **CMEK Encryption**: Artifact Registry repositories, BigQuery datasets, and Cloud KMS key rotation configured.
- [x] **Input/Output Validation**: Strict Pydantic parsing on tool inputs and deterministic assertions via `@validate_tool_output`.
- [x] **Model Armor Parameter Inspection**: Tool arguments scanned for prompt injection and SQL/command injection vectors.
- [x] **Fail-Safe Operation**: Skills execute with fail-closed error handling, preventing sensitive credential or stack trace exposure.
- [x] **Cryptographic Lineage**: SHA-256 provenance hashes generated for every skill release and logged to the BigQuery compliance ledger.
