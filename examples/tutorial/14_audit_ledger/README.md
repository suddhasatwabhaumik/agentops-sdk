# 14 · Audit Ledger: One Immutable BigQuery Row per Agent Action (`v1.0.0`)

**What you learn:** how to record who ran which SQL query, what `validate_sql_query` decided, execution outcome, row counts, and latency into BigQuery using `AuditLedger` + `BigQueryAuditAdapter`, linked to OpenTelemetry via `trace_id`.

```
question ──► validate_sql_query ──► BigQuery (if safe)
                  │
          AuditLogEntry {trace_id, session_id, user_id, agent_name, input_query, generated_sql,
                         execution_status SUCCESS|FAILED|BLOCKED, rows_returned, latency_ms, ...}
                  │
          await AuditLedger(sink=BigQueryAuditAdapter(project_id, dataset_id="agentops_governance",
                                                      table_id="audit_ledger")).log_entry(entry)
                  └──► DAY-partitioned streaming insert with bounded exponential Retry
```

## SDK calls used

| Call | What it does |
|---|---|
| `AuditLogEntry(...)` | Frozen Pydantic schema for one auditable agent/SQL action |
| `AuditLedger(sink=...)` | Validates, scrubs sensitive `error_message` values, and writes asynchronously (`log_entry`) or synchronously (`log_entry_sync`); defaults to single-stream `CloudLoggingAuditAdapter` when `sink=None` (`ADR-2`) |
| `BigQueryAuditAdapter(project_id, dataset_id="agentops_governance", table_id="audit_ledger", auto_create_table=True)` | Direct BigQuery streaming sink with bounded exponential `Retry` raising `AuditLedgerError` on failure (zero silent local `.jsonl` fallback) |
| `LocalFileAuditAdapter`, `InMemoryAuditAdapter` | Opt-in hardened local file sink (`0o600` + `O_NOFOLLOW`) and offline test adapter |

## GCP resources used

| Resource | Why | Access needed |
|---|---|---|
| BigQuery table `agentops_governance.audit_ledger` | Immutable governance audit table | `roles/bigquery.dataEditor` + `roles/bigquery.jobUser` |
| BigQuery table `agentops_tutorial.dealer_stock` | Audited sample table | `roles/bigquery.dataViewer` |

## Run

```bash
python examples/tutorial/14_audit_ledger/main.py
```

Expected: inserts one `SUCCESS` (`5` rows) and one `BLOCKED` (`0` rows) `AuditLogEntry` directly into `agentops_governance.audit_ledger`, then queries both rows back from BigQuery cleanly.
