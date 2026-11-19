# 06 · Row-Level Security: One Query, Each User Sees Only Their Rows (`v1.0.0`)

**What you learn:** how to enforce deterministic tenant isolation in SQL by rewriting the AST from verified caller claims inside `ExecutionScope` rather than trusting prompt text.

```
bind_execution_scope(user_id="dealer-de-4711", claims={"region": "Germany"})
          │
SQL ──► apply_row_level_policy(sql, claims, dialect="bigquery", policy_columns={"region": "country"})
          │ parse (sqlglot) ──► inject WHERE country = 'Germany'
          │                     (AND-ed with existing WHERE predicates, compound ORs parenthesized)
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

Default policy mapping is `{"tenant_id": "tenant_id"}`; `06_row_level_security/main.py` passes `policy_columns={"region": "country"}` explicitly so `claims["region"]` filters the BigQuery `country` column. Only verified `role in {"admin", "superadmin"}` bypasses row filtering (`ops-admin` passes `{"role": "admin"}`).

## GCP resources used

| Resource | Why | Access needed |
|---|---|---|
| BigQuery table `agentops_tutorial.dealer_stock` (includes `country` column) | Live execution of AST-rewritten queries | `roles/bigquery.dataViewer` + `roles/bigquery.jobUser` |

## Run

```bash
python examples/tutorial/06_row_level_security/main.py
```

Expected: the German dealer sees only `Germany`, the Brazilian dealer sees only `Brazil`, and `ops-admin` (`{"role": "admin"}`) sees all countries. Inspect `agent_run:secure_query` spans in Tempo to verify the rewritten SQL stored on `db.query.text`.
