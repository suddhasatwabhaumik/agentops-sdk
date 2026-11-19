<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# Architectural Blueprint: Connecting Autonomous Agents to Enterprise Data Hub (EDH) — MCP Virtualization vs. BigQuery Migration

> [!NOTE]
> **`v1.0.0` Grounding Status**: `v1.0.0` ships the hardened FastMCP governance gateway (`mcp_servers/src/server.py` with `query_database_tool` and `query_databricks_tool`), `SQLASTValidator` (`agentops_sdk.sql`), `@pass_user_identity` / `JWTAuthEngine` / `OutputDLPSanitizer` (`agentops_sdk.security`), `AuditLedger` (`agentops_sdk.audit`), and `FeatureStoreOnlineClient` (`agentops_sdk.feature_store`). Upstream REST connector services behind `${CONNECTOR_API_URL}` and EDH CDC pipelines below are architectural patterns built around that gateway boundary.

This document provides a comprehensive architectural comparison, trade-off analysis, and strategic recommendation for connecting enterprise AI agents to the **Enterprise Data Hub (EDH)**. It evaluates two primary approaches: **Option 1: Direct MCP-Based Virtualization** versus **Option 2: Continuous Replication & Migration to Google BigQuery**.

---

## 1. Executive Summary & Context

### 1.1 The Business Need
Enterprise AI agents (built on Vertex AI, Copilot Studio, LangGraph, or custom frameworks) require fast, secure, and governed access to enterprise analytical datasets residing in the **Enterprise Data Hub (EDH)** (e.g., Databricks Delta Lake, Hadoop/Spark, S3/ADLS/GCS data lakes, or on-premises warehouse appliances).

```
+---------------------------------------------------------------------------------------------------+
|                                  THE DATA ACCESS PARADIGM FOR AI AGENTS                           |
+---------------------------------------------------------------------------------------------------+
|                                                                                                   |
|     +-----------------------------------------+                                                   |
|     |         Autonomous AI Agents            |                                                   |
|     |  (Vertex AI Reasoning Engine / Gemini)  |                                                   |
|     +--------------------+--------------------+                                                   |
|                          |                                                                        |
|         +----------------+----------------+                                                       |
|         |                                 |                                                       |
|         v                                 v                                                       |
|  [OPTION 1: MCP VIRTUALIZATION]    [OPTION 2: BIGQUERY MIGRATION]                                 |
|  - Live Query Pushdown to EDH      - Continuous Ingestion into Native BQ                          |
|  - Zero Data Duplication           - Sub-Second Serverless Analytics                              |
|  - Managed via MCP Server & Hub    - Unified GCP Governance & DLP                                 |
|                                                                                                   |
+---------------------------------------------------------------------------------------------------+
```

---

## 2. Option 1: MCP-Based Direct Federation & Virtualization

In this architecture, data remains in the source EDH platform. A dedicated, containerized **EDH MCP Server** runs on Google Cloud Run, accepting standardized Model Context Protocol tool calls from the agent, validating SQL AST security rules, translating credentials, and dispatching live queries to EDH via JDBC/ODBC/REST over secure network tunnels.

### 2.1 Architecture Diagram (Option 1)

```mermaid
flowchart TD
    subgraph AgentLayer ["1. AI Agent Runtime"]
        Agent["Autonomous Agent
(Vertex AI Reasoning Engine / Gemini)"]
    end

    subgraph HubLayer ["2. Enterprise MCP Hub"]
        Hub["Centralized MCP Hub
(Tool Router & Auth Translator)"]
    end

    subgraph MCPServiceLayer ["3. EDH MCP Server (Cloud Run)"]
        MCPServer["EDH MCP Server
- FastMCP SSE Interface
- SQL AST Guardrail (sqlglot)
- Dynamic TTL Secret Cache
- Row-Level Security Injector"]
        Pool["ConnectionPoolManager
(Managed JDBC/ODBC Pool)"]
    end

    subgraph NetworkTunnel ["4. Secure Hybrid Connectivity"]
        Tunnel["Cloud Interconnect / Cloud VPN
/ Private Service Connect (PSC)"]
    end

    subgraph EDHSource ["5. Enterprise Data Hub (EDH)"]
        Databricks["Databricks SQL Warehouse
/ Spark Cluster / Delta Lake"]
        DataLake[("Cloud Storage / ADLS / S3
Raw & Curated Delta Tables")]
    end

    Agent -->|Tool Call: query_edh_tool| Hub
    Hub -->|SSE / Private Route| MCPServer
    MCPServer --> Pool
    Pool -->|Parameterized Query| Tunnel
    Tunnel --> Databricks
    Databricks <--> DataLake
    Databricks -->> Tunnel: Result Rows (Streaming)
    Tunnel -->> MCPServer: Unpack DataFrame
    MCPServer -->> Hub: MCP JSON-RPC Response
    Hub -->> Agent: Structured Markdown Context
```

### 2.2 Technical Workflow
1. **Tool Invocation**: The agent determines it needs historical inventory metrics and calls `query_edh_inventory_tool`.
2. **Security & AST Parsing**: The `SQLASTValidator` on the MCP server parses the query, validates that it is strictly a read-only `SELECT`, verifies table allowlists, and applies tenant row-level policy rewrites.
3. **Federated Query Execution**: The connection pool acquires a pooled connection over Private Service Connect and dispatches the query to Databricks/EDH.
4. **Result Streaming & DLP Sanitization**: Results stream back to the MCP server, undergo Cloud DLP masking for sensitive fields, and return to the LLM context.

---

## 3. Option 2: Continuous Ingestion & Migration to Google BigQuery

In this architecture, analytical datasets from EDH are continuously ingested, synchronized, or replicated into **Google BigQuery** (or federated via **BigLake** open table formats). AI agents query BigQuery through the **Hardened FastMCP Governance Gateway (`mcp_servers/src/server.py`)** backed by an upstream Cloud Run **BigQuery Connector Service (`CONNECTOR_API_URL`)**.

### 3.1 Architecture Diagram (Option 2)

```mermaid
flowchart TD
    subgraph EDHSource ["1. Enterprise Data Hub (EDH Source)"]
        SourceLake[("EDH Delta Lake / S3 / ADLS / On-Prem
Operational & Transactional Tables")]
    end

    subgraph IngestionPipeline ["2. Continuous Ingestion & Replication Pipeline"]
        CDC["Datastream (CDC) /
Cloud Dataflow (Apache Beam)"]
        DeltaShare["Delta Sharing Connector /
Storage Transfer Service (STS)"]
    end

    subgraph GoogleBigQuery ["3. Google BigQuery Data Platform"]
        BigLake["BigLake Storage Engine
(Open Formats: Delta, Iceberg, Parquet)"]
        BQNative[("BigQuery Native Managed Storage
- Optimized Capacitor Format
- BI Engine In-Memory Acceleration
- Automatic Partitioning & Clustering")]
        DataCatalog["Enterprise Data Catalog
(Enterprise Metadata Catalog & Policy Engine)"]
    end

    subgraph ConnectorLayer ["4. MCP Gateway & Upstream Connector (Cloud Run)"]
        Connector["mcp_servers/src/server.py + Upstream Connector
- HeaderBindingMiddleware & MCP_REQUIRE_AUTH=true
- SecretManagerStore TTL Cache
- agentops_sdk.sql (SQLASTValidator & RLS)"]
    end

    subgraph AgentLayer ["5. AI Agent Runtime"]
        Agent["Autonomous Agent
(Vertex AI Agent Engine / Reasoning Engine / Gemini)"]
    end

    SourceLake -->|Change Data Capture (CDC)| CDC
    SourceLake -->|Replication / Federation| DeltaShare
    CDC --> BQNative
    DeltaShare --> BigLake
    BigLake <--> BQNative
    BQNative <--> DataCatalog

    Agent -->|Execute SQL / Tool Call| Connector
    Connector -->|Sub-Second Native Query| BQNative
    BQNative -->> Connector: Arrow / Vectorized Batch Response
    Connector -->> Agent: High-Speed Structured JSON Response
```

### 3.2 Technical Workflow
1. **Continuous Replication**: Datastream / Cloud Dataflow captures change data from external EDH tables and streams it into partitioned BigQuery tables with low sync lag ($< 5$ minutes).
2. **BigLake Federation (Zero-Copy Alternative)**: For Delta Lake / Iceberg tables stored on Cloud Storage, BigQuery queries data directly in place via BigLake external tables with **zero data duplication (0 bytes in BigQuery storage)** and uniform GCP IAM enforcement.
3. **Sub-Second Agent Querying**: Agents query BigQuery native or BigLake tables through the FastAPI connector, achieving query response times under 800ms.

### 3.3 Zero-ETL Demarcation & Downstream Process Cost Breakdown

#### 1. Defining the Zero-ETL Boundary
- **Zero-ETL / Zero-Copy Layer**: When leveraging **BigLake Delta Native Federation**, analytical queries scan GCS Delta Lake storage directly in-place without moving data into BigQuery managed storage. This access tier is **100% Zero-ETL and Zero-Copy**.
- **Transformation & Feature Engineering Layer**: Creating derived features, embeddings, aggregations, training dataset extractions, and syncing online stores represent **computational ML transformations**, not base-table ETL pipelines. While the base data requires zero ETL, derived feature materialization consumes standard compute and storage resources.

#### 2. BigQuery Cost Breakdown Across Key Processes
- **Feature Materialization**: Compute is billed for query execution (slots or on-demand bytes scanned). Storage is billed only if saved as a persistent BigQuery table (\$0.02/GB/month active, \$0.01/GB/month long-term); logical SQL views incur **\$0.00 storage**.
- **Training Datasets**: Compute is billed during dataset extraction. Streaming directly into ML frameworks via the BigQuery Storage Read API incurs \$1.10/TB read (first **300 TB/month are free**). Exporting directly to GCS via `EXPORT DATA` incurs **\$0.00 BigQuery storage**.
- **Feature Store Synchronization**: Scheduled sync queries consume standard BigQuery compute. Storage in BigQuery is **\$0.00**; online serving is billed under Vertex AI Feature Store node hours.
- **Cached Query Results**: **\$0.00 Compute & \$0.00 Storage**. Deterministic 24-hour result caching serves identical repeated queries for free.
- **Derived Feature Tables**: Compute is billed on refresh. Persistent tables incur standard BigQuery storage; Authorized Views incur **\$0.00 storage**.
- **Model Artifacts**: **\$0.00 in BigQuery**. ML model serializations (weights, `.pt`, `.pkl`, ONNX) reside in **Google Cloud Storage** or Vertex AI Model Registry.

#### 3. Enterprise Data Catalog Governance Architecture & CDAM Decision
- **Enterprise Data Catalog as the Central Authority**: In alignment with Google Customer Enterprise Architecture, Enterprise Data Catalog is the central data catalog and governance system.
- **Option A (Baseline - Recommended)**: Enterprise Data Catalog's existing BigQuery Scanner extracts metadata and lineage into the central governance catalog, while access is enforced natively using Google Cloud IAM and Authorized Views. **Requires zero new CDAM licensing**.
- **Option B (Optional Extension)**: Enterprise Data Catalog Cloud Data Access Management (CDAM) automates policy pushdown into BigQuery, managed via catalog processing units.
- **Decision Demarcation**: *The adoption of CDAM vs. native GCP IAM enforcement is an internal architectural, governance, and commercial decision for Google Customer EA and Data Governance teams.*

---

## 4. Comprehensive Evaluation Matrix: Option 1 vs. Option 2

| Evaluation Dimension | Option 1: Direct MCP Virtualization | Option 2: BigQuery Migration / Replication | Winner / Recommendation |
|---|---|---|:---:|
| **Query Latency (P50 / P95)** | **High Latency (3s – 15s+)**<br>Cold cluster spin-up, cross-cloud egress hops, multi-layered protocol overhead. | **Sub-Second (300ms – 1.2s)**<br>Serverless distributed compute, BI Engine in-memory acceleration, zero cold-starts. | 🏆 **Option 2 (BigQuery)** |
| **Data Freshness** | **Real-Time (0 sync lag)**<br>Queries source EDH tables directly at execution time. | **Near Real-Time (1 – 5 min lag)**<br>Dependent on CDC streaming / batch micro-batch pipelines (instant for BigLake Delta log read). | 🏆 **Option 1 (MCP)** |
| **Agent Reasoning Flow & Ergonomics** | **Risk of Timeouts**<br>Long latency can cause multi-turn agents or Copilot Studio gateways to time out ($> 15\text{s}$). | **Ultra-Fast Multi-Turn Loops**<br>Enables rapid 5+ turn iterative tool calling without user perceived delay. | 🏆 **Option 2 (BigQuery)** |
| **Data Duplication & Storage Cost** | **Zero Duplication**<br>No additional storage costs; compute charged per EDH warehouse runtime. | **Zero Duplication with BigLake**<br>Queries Delta Lake in GCS in-place (0 bytes in BQ). *(Replicated CDC tables store copy in BQ).* | 🤝 **Tie with BigLake** |
| **Query & Compute Cost (FinOps)** | **High per-query compute**<br>Keeping Databricks / Spark SQL warehouses active for intermittent agent turns is expensive. | **Predictable / Pay-per-Query**<br>On-demand slot pricing (\$6.25/TB scanned) or reserved editions with autoscaling. | 🏆 **Option 2 (BigQuery)** |
| **Security & Fine-Grained Access** | **Complex Cross-Cloud RBAC**<br>Requires mapping OAuth/JWT identities to EDH service principals and token vaults. | **Native GCP IAM & Enterprise Data Catalog Governance**<br>Native column-level security, dynamic masking, unified Row-Level Security (RLS), registered in Enterprise Data Catalog. | 🏆 **Option 2 (BigQuery)** |
| **Compliance & DLP Sanitization** | **Client-Side Sanitization**<br>Must intercept and inspect raw payload streams in the MCP container. | **Serverless Native DLP**<br>BigQuery native Cloud DLP profiling and automated classification tags. | 🏆 **Option 2 (BigQuery)** |
| **Operational & Maintenance Complexity** | **High Runtime Dependency**<br>Agent availability is tightly coupled to EDH cluster uptime and network tunnels. | **Decoupled & Highly Resilient**<br>Google-managed 99.99% SLA serverless platform with zero cluster administration. | 🏆 **Option 2 (BigQuery)** |
| **Scalability (Agent Swarm Load)** | **Bottlenecked by Cluster Concurrency**<br>50+ concurrent agents can saturate JDBC connection limits and queue queries. | **Virtually Infinite Scaling**<br>Handles thousands of concurrent queries with automated slot allocation. | 🏆 **Option 2 (BigQuery)** |

---

## 5. Detailed Trade-offs & Deep-Dive Analysis

### 5.1 Option 1 (Direct MCP Virtualization) Deep-Dive

#### Advantages (Pros):
1. **Zero Data Duplication & Single Source of Truth**: Eliminates ETL pipeline maintenance, data drift, and redundant storage footprint.
2. **Immediate Time-to-Value (TTV)**: Deploying an MCP server wrapper takes days, without waiting for enterprise data migration approvals.
3. **Real-Time Data State**: Ideal for operational use cases where second-level accuracy is mandatory (e.g., live warehouse pallet scans).

#### Disadvantages & Critical Risks (Cons):
1. **Latency Bottlenecks for LLM Tool Chains**: When an agent executes a multi-step Plan-and-Solve loop requiring 4 tool calls, a 10-second EDH query latency creates a **40-second total response time**, causing poor user experience and gateway timeouts.
2. **Compute Idle Costs**: Keeping high-concurrency Spark/Databricks SQL warehouses warm 24/7 for sporadic agent queries results in high infrastructure spend.
3. **Cross-Network Vulnerability**: Heavy dependency on hybrid VPN / Dedicated Interconnect stability.

---

### 5.2 Option 2 (BigQuery Migration & Replication) Deep-Dive

#### Advantages (Pros):
1. **Blazing Fast Response Times**: BigQuery's columnar storage (Capacitor) and in-memory BI Engine deliver results in **$< 800\text{ms}$**, unlocking fluid, real-time agent conversations.
2. **Unified GCP AI Ecosystem Integration**: Native integration with Vertex AI Feature Store, Vertex AI Vector Search, Vertex Reasoning Engine, and Enterprise Data Catalog.
3. **Superior Governance & Security**: Native column-level masking, automated PII discovery via Cloud DLP, and seamless row-level access control enforced at the database engine level, audited centrally in Enterprise Data Catalog.
4. **FinOps Cost Optimization**: On-demand query pricing ensures zero cost when agents are idle, dramatically reducing compute bills compared to dedicated external warehouses.

#### Disadvantages & Mitigations (Cons):
1. **Data Pipeline Maintenance**: Requires establishing and monitoring Datastream CDC or Dataflow pipelines.
   * *Mitigation*: Leverage **BigLake / Delta Sharing** for zero-copy federation where data pipelines are unfeasible.
2. **Sync Lag**: Replicated tables may have 1 to 5 minutes of sync delay.
   * *Mitigation*: Analytical queries for business intelligence and agent reasoning are rarely impacted by 2-minute sync lag.

---

## 6. The Strategic Recommendation: "Hot/Cold Hybrid Tiering" Pattern

Google Cloud recommends a **Hybrid Tiering Strategy** that combines the strengths of both approaches:

```
+---------------------------------------------------------------------------------------------------+
|                              RECOMMENDED HYBRID ARCHITECTURAL TIERING                             |
+---------------------------------------------------------------------------------------------------+
|                                                                                                   |
|  [AI AGENT LAYER]                                                                                 |
|         |                                                                                         |
|         +-----------------------+-----------------------+                                         |
|                                 |                                                                 |
|                                 v                                                                 |
|             [TIER 1: HOT / OPERATIONAL ANALYTICAL DATA]                                           |
|             - Orders, Inventory, Customer Master, Parts Catalog, Fleet Telemetry                  |
|             - REPLICATED TO NATIVE GOOGLE BIGQUERY (Datastream / Dataflow CDC)                    |
|             - Response Time: < 800ms | High Concurrency | Pay-per-Query                           |
|                                 |                                                                 |
|                                 v                                                                 |
|             [TIER 2: COLD / AD-HOC DEEP ARCHIVAL DATA]                                            |
|             - Unstructured Telemetry Logs, 10-Year Historical Archives, Niche Silos               |
|             - ACCESSED VIA EDH MCP SERVER (Live Query Pushdown)                                   |
|             - Response Time: 5s - 15s | On-Demand Execution | Zero Data Duplication              |
|                                                                                                   |
+---------------------------------------------------------------------------------------------------+
```

### Architectural Decision Framework:
```
IF (Query Frequency == High (> 50 calls/day) OR Latency Requirement < 2 seconds OR Table Size < 50TB):
    --> Route to OPTION 2: BigQuery Native / Replicated Table
ELSE IF (Data Sensitivity prevents copying OR Table is Deep Ad-Hoc Archive (> 100TB) OR 0 Sync Lag is Mandatory):
    --> Route to OPTION 1: EDH MCP Server (Direct Virtualization)
```

---

## 7. Phased Implementation & Migration Roadmap

```
Phase 1: Immediate MVP via EDH MCP Server (Weeks 1 - 2)
  ├── Deploy hardened FastMCP Gateway (mcp_servers/src/server.py) on Cloud Run
  ├── Connect to Databricks / EDH via validated HTTPS CONNECTOR_API_URL over Private Service Connect
  ├── Enforce agentops_sdk.sql.SQLASTValidator, HeaderBindingMiddleware Bearer auth, & @pass_user_identity
  └── Enable immediate agent capability for initial testing and validation

Phase 2: Establish CDC & High-Speed BigQuery Tier (Weeks 3 - 5)
  ├── Identify Top 5 High-Impact Agent Datasets (Orders, Inventory, Customers, Parts, Telemetry)
  ├── Configure Datastream / Cloud Dataflow continuous replication into BigQuery
  ├── Configure BigQuery table partitioning, clustering, and BI Engine reservation
  └── Route primary agent queries through mcp_servers/src/server.py -> BigQuery Upstream Connector

Phase 3: Unified Lakehouse Governance with BigLake & Enterprise Data Catalog (Weeks 6 - 8)
  ├── Enable BigLake tables for zero-copy Delta Lake / Iceberg external access
  ├── Enforce unified Row-Level Security (RowLevelPolicyEngine) & OutputDLPSanitizer masking
  ├── Sync BigQuery catalog metadata into Enterprise Data Catalog scanner
  ├── Connect AuditLedger (CloudLoggingAuditAdapter / BigQueryAuditAdapter) to Cloud Trace & Cloud Logging
  └── Complete production cutover to the Hybrid Tiering architecture
```
