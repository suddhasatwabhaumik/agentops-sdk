# 05 · SQL Guard: LLM-Written SQL Runs Only If the AST Is Safe (`v1.0.0`)

**What you learn:** how to let an LLM generate analytical SQL while guaranteeing via AST parsing (`sqlglot>=30.0.0`) that only read-only `SELECT` statements targeting allowlisted tables reach BigQuery.

```
question ──► Gemini ──► SQL text ──► validate_sql_query / @validate_sql_query_decorator
                                          │ parse with sqlglot → AST
                                          ├─ statement type must be read-only SELECT
                                          ├─ every referenced table matches allowed_tables
                                          ├─ no INFORMATION_SCHEMA / system schemas or stacked DDL/DML
                                          ▼
                             safe ──► BigQuery job (real) ──► rows
                             unsafe ► raises SQLGovernanceError, span sql.ast.is_safe=false, zero BigQuery jobs
```

## SDK calls used

| Call | What it does |
|---|---|
| `validate_sql_query(sql, *, dialect="bigquery", allowed_tables=None)` | Returns `SQLValidationResult` (`is_safe`, `sanitized_sql`, `violation_reason`, `referenced_tables`) |
| `@validate_sql_query_decorator(*, dialect="bigquery", allowed_tables=None, sql_params=["sql"], auto_sanitize=True)` | Validates the named SQL argument before executing the decorated query function; raises `SQLGovernanceError` if unsafe |
| `SQLASTValidator(*, dialect="bigquery", allowed_tables=None)` | Configurable class instance behind both helpers |

Allowlist patterns accept exact `project.dataset.table` identifiers or boundary-anchored wildcards (`project.dataset.*`). Unqualified table names (`FROM inventory_stock`) are blocked whenever `allowed_tables` is configured.

## GCP resources used

| Resource | Why | Access needed |
|---|---|---|
| BigQuery table `agentops_tutorial.inventory_stock` | Approved target table (provisioned via `00_setup/provision.py bigquery`) | `roles/bigquery.dataViewer` on the dataset, `roles/bigquery.jobUser` on the project |
| Vertex AI, Gemini | Generates the SQL queries in cases 1 & 2 | `roles/aiplatform.user` |

Set `TUTORIAL_BQ_TABLE` to target a different table (must include a `country` column for Tutorial `06`).

## Run

```bash
python examples/tutorial/05_sql_guard/main.py
```

Expected: case 1 passes AST validation and queries BigQuery cleanly; case 2 (`finance.credit_limits`) raises `SQLGovernanceError` before creating any BigQuery job; case 3 (stacked `DROP TABLE`) returns `is_safe=False`.
