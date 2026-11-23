# 02 · Case Telemetry: Business Records per Use Case in BigQuery (`v1.0.0`)

**What you learn:** how to record operational use-case metrics (transactions processed, stage-by-stage latencies in `stages: dict[str, float]`, business KPI, P&L impact line) into Cloud Logging and BigQuery, correlated with OTel traces.

```
reconcile_orders() ── with UseCaseTracker("tenant_order_reconciliation", exporter=CompositeTelemetryExporter(...))
      tracker.set_metadata("tenant_id", ...)
      start_stage / end_stage × 3, add_transactions(42)
      on exit ──► UseCaseTelemetry record {case_id, use_case_name, stages, total_latency_ms, business_kpi, trace_id}
                        │
                        ├──► CloudLoggingTelemetryExporter  ──► agentops-use-case-telemetry
                        └──► BigQueryTelemetryExporter      ──► agentops_observability.use_case_telemetry
Spans of the same run ──► OTLP ──► Tempo   (join key: trace_id)
```

## SDK calls used

| Call | What it does |
|---|---|
| `UseCaseTracker(use_case_name, *, exporter=..., business_kpi=..., pl_impact_line=...)` | Sync/async context manager emitting `UseCaseTelemetry` cleanly on exit |
| `tracker.start_stage(name)` / `tracker.end_stage(name)` | Records stage duration (ms) in `record.stages[name]` |
| `tracker.add_transactions(count)` / `tracker.set_metadata(key, val)` | Updates `transactions_processed` and custom case metadata |
| `CloudLoggingTelemetryExporter(project_id=...)` | Single-stream default exporter emitting structured logs to `agentops-use-case-telemetry` |
| `BigQueryTelemetryExporter(project_id, dataset_id, table_id, auto_create_table=True)` | Streaming insert to BigQuery (day-partitioned on `timestamp`) |
| `CompositeTelemetryExporter([...])` | Fans out each `UseCaseTelemetry` record explicitly to multiple sinks |

> [!NOTE]
> By default (`ADR-2`), `UseCaseTracker` emits to `CloudLoggingTelemetryExporter` alone. When you also want direct streaming into BigQuery (or GCS via `BatchedGCSTelemetryExporter`), wire `CompositeTelemetryExporter` explicitly as shown in `main.py`.

## GCP resources used

| Resource | Why | Access needed |
|---|---|---|
| BigQuery dataset `agentops_observability`, table `use_case_telemetry` | Persistent per-case analytics row | `roles/bigquery.dataEditor` (insert), `roles/bigquery.jobUser` (read back) |
| Cloud Logging log `agentops-use-case-telemetry` | Primary Cloud Log Router event stream | `roles/logging.logWriter` |

## Run

```bash
python examples/tutorial/02_case_telemetry/main.py
```

Expected: prints `BigQuery row: {'use_case_name': 'tenant_order_reconciliation', 'transactions_processed': 42, ...}`. Without BigQuery write access the exporter logs an error and exits non-zero; see [`../ACCESS.md`](../ACCESS.md).
