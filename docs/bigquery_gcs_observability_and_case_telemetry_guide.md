<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# BigQuery & GCS Observability & Per-Case Telemetry Architecture Guide (`v1.0.0`)

**Version:** 1.0.0  
**Status:** Active Production Standard  
**Target Modules:** `agentops_sdk.telemetry`, `tools/observability/`  

---

## 1. Overview & Business Context

To provide unified **production observability and per-case telemetry**, the AgentOps platform delivers two complementary pipelines:

1. **Infrastructure Log Router Sinks & Export Utilities (`tools/observability/`)**: Stream structured FinOps and case telemetry logs from Google Cloud Logging into day-partitioned BigQuery analytics tables and cold-storage Cloud Storage archives.
2. **Per-Case Telemetry Tracking (`UseCaseTracker`, `@trace_use_case`)**: Record transaction counts, stage-by-stage latencies (`stages: dict[str, float]`), business KPIs, P&L budget lines, and success/error outcomes via typed `CaseTelemetryExporterPort` adapters.

### Single-Stream Default & Explicit Fan-Out (`ADR-2`)

In `v1.0.0`, `UseCaseTracker` and `@trace_use_case` default to **single-stream `CloudLoggingTelemetryExporter`** (`agentops-use-case-telemetry`). This avoids redundant 4-destination writes or hot-path BigQuery latency by default while letting teams opt in explicitly to `BigQueryTelemetryExporter`, `BatchedGCSTelemetryExporter`, `CompositeTelemetryExporter`, `LocalFileTelemetryExporter` (`0o600` + `O_NOFOLLOW`), or `InMemoryTelemetryExporter` for tests.

---

## 2. Architecture & Data Flow

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                AgentOps Application Layer                                        │
│                                                                                                  │
│   ┌─────────────────────────────────────────────────┐   ┌────────────────────────────────────┐   │
│   │   @trace_llm_call / @trace_agent_flow           │   │   UseCaseTracker / @trace_use_case │   │
│   │   (FinOps & Token Telemetry)                    │   │   (Case Stage Latencies & Volume)  │   │
│   └────────────────────────┬────────────────────────┘   └─────────────────┬──────────────────┘   │
└────────────────────────────┼──────────────────────────────────────────────┼──────────────────────┘
                             │                                              │
                 FinOps Cloud Log (gcp/hybrid)               Single-Stream Default or Explicit Exporter
                             │                         ┌────────────────────┼────────────────────┐
                             ▼                         ▼                    ▼                    ▼
                ┌────────────────────────┐  ┌────────────────┐  ┌──────────────────┐  ┌────────────────┐
                │  Google Cloud Logging  │  │ Cloud Logging  │  │  Direct BigQuery │  │  Batched GCS   │
                │  agentops-finops-      │  │ agentops-use-  │  │  Partitioned     │  │  NDJSON Archive│
                │  telemetry             │  │ case-telemetry │  │  Table Insert    │  │  (batch_size)  │
                └────────────┬───────────┘  └────────────────┘  └─────────┬────────┘  └────────┬───────┘
                             │                                            │                    │
               ┌─────────────┴─────────────┐                              │                    │
               ▼                           ▼                              ▼                    ▼
    ┌──────────────────────┐   ┌────────────────────────────────────────────────────────┐
    │ Log Router Sink:     │   │ Google Cloud Storage                                   │
    │ BigQuery Dataset     │   │ Bucket: `gs://<bucket_name>/`                          │
    │ `agentops_           │   │  - Log Sinks: `syslog/YYYY/MM/DD/...`                  │
    │  observability`      │   │  - Per-Case: `{prefix}/{use_case}/{YYYY-MM-DD}/*.ndjson`
    └──────────────────────┘   └────────────────────────────────────────────────────────┘
```

### Pipeline Comparison

| Component | Passive Log Router Pipeline | Active Case Telemetry (`UseCaseTracker`) | On-Demand Export CLI (`tools/observability`) |
| :--- | :--- | :--- | :--- |
| **Ingress Point** | Cloud Logging client | `agentops_sdk.telemetry` | `python -m tools.observability.log_router_admin` |
| **Default Behavior** | Sink filter match | Single-stream `CloudLoggingTelemetryExporter` | Interactive or cron query + export |
| **Explicit Destinations** | BigQuery dataset, GCS bucket | BigQuery, Batched GCS, Composite, Local JSONL, InMemory | BigQuery, GCS, Local JSONL (`--output-file`) |
| **GCP IAM Needed** | `roles/logging.configWriter` | `roles/logging.logWriter` / `bigquery.dataEditor` / `storage.objectCreator` | `roles/logging.viewer` + target write permissions |

---

## 3. Domain Model & Storage Layouts

### 3.1 `UseCaseTelemetry` (`agentops_sdk.domain.models`)
```python
class UseCaseTelemetry(BaseModel):
    case_id: str
    use_case_name: str
    session_id: str = ""
    user_id: str = ""
    trace_id: str = ""
    span_id: str = ""
    transactions_processed: int = 0
    stages: dict[str, float] = Field(default_factory=dict)  # stage_name -> elapsed_ms
    total_latency_ms: float = 0.0
    success: bool = True
    error_message: str | None = None
    business_kpi: str = "agent-execution"
    pl_impact_line: str = "ai-platform-opex"
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: str  # UTC ISO-8601
```

### 3.2 BigQuery Table Schema (`USE_CASE_BQ_SCHEMA`)
Target table defaults to `<project_id>.agentops_telemetry.use_case_telemetry` (configurable via `dataset_id` / `table_id`), **DAY-partitioned on `timestamp`** via `PartitionedBQWriter` with bounded exponential `Retry(initial=0.5, maximum=10.0, multiplier=2.0, deadline=30.0)`:

```sql
CREATE TABLE IF NOT EXISTS `<project_id>.<dataset_id>.<table_id>` (
    case_id STRING NOT NULL,
    use_case_name STRING NOT NULL,
    session_id STRING,
    user_id STRING,
    trace_id STRING,
    span_id STRING,
    transactions_processed INT64,
    stages JSON,
    total_latency_ms FLOAT64,
    success BOOL,
    error_message STRING,
    business_kpi STRING,
    pl_impact_line STRING,
    metadata JSON,
    timestamp TIMESTAMP NOT NULL
)
PARTITION BY DATE(timestamp);
```

### 3.3 `BatchedGCSTelemetryExporter` Layout
`BatchedGCSTelemetryExporter(bucket_name, prefix="use-case-telemetry", batch_size=50, ...)` buffers records cleanly under a lock and flushes NDJSON objects (call `.flush()` or `.close()` / `with exporter:` before exit) to sanitized paths:

```text
gs://<bucket_name>/<prefix>/<use_case_name>/<YYYY-MM-DD>/<case_id>_<uuid8>.ndjson
```
Path segments are sanitized via `sanitize_gcs_segment()` to neutralize `/` and `..` traversal sequences.

---

## 4. Admin & Developer Tooling (`tools/observability/`)

### 4.1 Python Module CLI (`tools/observability/log_router_admin.py`)
Global flags (`--project`, `--region`, `--dataset`, `--bucket`) precede the subcommand (`setup`, `list-sinks`, `export`, `verify`):

```bash
# 1. Configure BigQuery and GCS Log Router sinks + IAM writer bindings
python -m tools.observability.log_router_admin \
  --project <your-gcp-project-id> \
  --region europe-west4 \
  --dataset agentops_observability \
  --bucket <your-gcp-project-id>-agentops-observability-logs \
  setup

# 2. List configured Log Router sinks
python -m tools.observability.log_router_admin \
  --project <your-gcp-project-id> \
  list-sinks

# 3. Export recent logs on demand to BigQuery, GCS, and/or local JSONL
python -m tools.observability.log_router_admin \
  --project <your-gcp-project-id> \
  --dataset agentops_observability \
  --bucket <your-gcp-project-id>-agentops-observability-logs \
  export --hours 24 --limit 500 --output-file /tmp/recent_logs.jsonl

# 4. Emit a synthetic FinOps test log for sink verification
python -m tools.observability.log_router_admin \
  --project <your-gcp-project-id> \
  verify
```

### 4.2 Shell Automation Scripts
*   `tools/observability/setup_log_routers.sh <PROJECT_ID> [REGION] [DATASET_ID] [BUCKET_NAME] [BQ_SINK] [GCS_SINK]`
*   `tools/observability/export_logs.sh <PROJECT_ID>` (configured via env vars `REGION`, `DATASET_ID`, `BUCKET_NAME`, `HOURS`, `LIMIT`, `OUTPUT_FILE`)

---

## 5. SDK Instrumentation Runbook

### 5.1 Stage Breakdowns with `UseCaseTracker`
Use `UseCaseTracker` as a sync (`with`) or async (`async with`) context manager whenever recording `start_stage` / `end_stage`, `add_transactions`, and `set_metadata`:

```python
from agentops_sdk.telemetry import (
    BatchedGCSTelemetryExporter,
    BigQueryTelemetryExporter,
    CloudLoggingTelemetryExporter,
    CompositeTelemetryExporter,
    UseCaseTracker,
)

with BatchedGCSTelemetryExporter("my-telemetry-bucket", batch_size=10) as gcs_exp:
    exporter = CompositeTelemetryExporter(
        [
            CloudLoggingTelemetryExporter(project_id="my-gcp-project"),
            BigQueryTelemetryExporter(
                project_id="my-gcp-project",
                dataset_id="agentops_observability",
            ),
            gcs_exp,
        ]
    )
    with UseCaseTracker(
        "support_case_triage",
        exporter=exporter,
        business_kpi="support_case_triage_time_reduction",
        pl_impact_line="operational_compliance",
    ) as tracker:
        tracker.start_stage("intake_and_validation")
        tracker.end_stage("intake_and_validation")
        tracker.add_transactions(1)

        tracker.start_stage("root_cause_analysis")
        tracker.end_stage("root_cause_analysis")
        tracker.set_metadata("severity", "high")
```

### 5.2 Wrapping Functions with `@trace_use_case`
For functions that do not need manual per-stage markers, `@trace_use_case` wraps both sync and async callables cleanly in a `UseCaseTracker` lifecycle (measuring `total_latency_ms`, `success`, and scrubbed `error_message` automatically):

```python
from agentops_sdk.telemetry import trace_use_case


@trace_use_case(
    "supplier_quote_batch",
    business_kpi="procurement_cost_reduction",
    pl_impact_line="cogs_reduction",
)
async def process_batch(batch_id: str) -> dict[str, str]:
    return {"batch_id": batch_id, "status": "completed"}
```

---

## 6. Verification & Test Suites

```bash
# Hermetic unit & contract suites (zero GCP calls required)
pytest tests/unit/test_telemetry_exporters.py tests/unit/test_tools_cli.py --eval-mode=in_memory -v

# Live 4-tier GCP observability E2E verification harness
./tests/e2e_observability_and_case_telemetry/run_all_observability_tests.sh <your-gcp-project-id>
```

Available exporters for tests and hardened local debugging:
- `InMemoryTelemetryExporter()` (`.records`, `.clear()`)
- `LocalFileTelemetryExporter(file_path, base_dir=..., max_bytes=..., backup_count=...)` (enforces `0o600`, `O_NOFOLLOW`, and `realpath` containment)
