# 06 · Row-Level Security: One Query, Each User Sees Only Their Rows (`v1.0.0`)

**What you learn:** how to enforce deterministic tenant isolation in SQL by scope-aware AST rewriting (`sqlglot>=30.0.0`) from verified caller claims inside `ExecutionScope` rather than trusting prompt text.

```
bind_execution_scope(user_id="tenant-de-001", claims={"region": "Germany"})
          │
SQL ──► apply_row_level_policy(sql, claims, dialect="bigquery", policy_columns={"region": "country"})
          │ parse (sqlglot>=30.0.0) ──► inject WHERE country = 'Germany' / LEFT JOIN ... ON
          │                             (dialect-normalized CTEs, sole-FROM ML.PREDICT, dataset.*, post-rewrite check)
          │ role in {"admin", "superadmin"} → bypasses tenant filter
          ▼
    BigQuery executes the rewritten SQL
```

## SDK calls used

| Call | What it does |
|---|---|
| `bind_execution_scope(user_id=..., claims=...)` | Binds caller identity and verified claims into the async-safe `ExecutionScope` |
| `apply_row_level_policy(sql, claims, *, dialect="bigquery", policy_columns=None, target_tables=None)` | Returns the rewritten SQL string scoped to the caller's claims |
| `RowLevelPolicyEngine(*, dialect="bigquery", policy_columns=None, target_tables=None).apply(sql, claims)` | Stateful class instance when reusing custom claim-to-column mappings |

Default policy mapping is `{"tenant_id": "tenant_id"}` (with Entra `"tid"` mapped to `"tenant_id"`); `06_row_level_security/main.py` passes `policy_columns={"region": "country"}` explicitly so `claims["region"]` filters the BigQuery `country` column. Unsafe constructs (`TableFromRows`, `PIVOT`, `UNPIVOT`, column-list aliases) are rejected, and only verified `role in {"admin", "superadmin"}` bypasses row filtering (`ops-admin` passes `{"role": "admin"}`).

## GCP resources used

| Resource | Why | Access needed |
|---|---|---|
| BigQuery table `agentops_tutorial.tenant_inventory` (includes `country` column) | Live execution of AST-rewritten queries | `roles/bigquery.dataViewer` + `roles/bigquery.jobUser` |

## Run

```bash
python examples/tutorial/06_row_level_security/main.py
```

Expected: the German tenant sees only `Germany`, the Brazilian tenant sees only `Brazil`, and `ops-admin` (`{"role": "admin"}`) sees all countries. Inspect `agent_run:secure_query` spans in Tempo to verify the rewritten SQL stored on `db.query.text`.
