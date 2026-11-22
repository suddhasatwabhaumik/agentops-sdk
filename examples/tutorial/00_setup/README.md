# 00 · Setup: check access, provision once, keep it running

Two standalone setup scripts (no SDK calls):

| Script | What it does | Changes GCP? |
|---|---|---|
| `preflight.py` | Reports `READY` or `BLOCKED` per tutorial and names the missing permission, API, or resource | No |
| `provision.py <resource>` | Provisions or verifies shared resources: APIs, BigQuery table, Model Armor templates, Agent Engine (Memory Bank), Cloud SQL | Yes (idempotent, never deletes) |

```
preflight.py ──► ADC identity ──► testIamPermissions, serviceusage, GET on each resource ──► READY / BLOCKED table
provision.py ──► resources/apis.py           serviceusage :enable
             ──► resources/bigquery_table.py google-cloud-bigquery (agentops_tutorial.inventory_stock, 5 rows)
             ──► resources/model_armor.py    GET verifies centrally provisioned agentops-input-guard / -output-guard
             ──► resources/agent_engine.py   vertexai.Client().agent_engines.create(context_spec.memory_bank_config)
             ──► resources/cloudsql.py       gcloud sql instances / databases / users (_ensure_user preserves existing passwords)
```

## Run

```bash
cp examples/tutorial/.env.example examples/tutorial/.env     # edit project/region if needed
python examples/tutorial/00_setup/preflight.py
python examples/tutorial/00_setup/provision.py agent-engine  # works with roles/aiplatform.user
python examples/tutorial/00_setup/provision.py all           # remaining steps require grants in ../ACCESS.md
```

- Model Armor templates (`agentops-input-guard` and `agentops-output-guard`) are provisioned via central infrastructure IaC; `provision.py model-armor` verifies access (`roles/modelarmor.viewer` / `roles/modelarmor.user`).
- After `agent-engine`, copy the printed resource name into `MEMORY_BANK_NAME` in `.env`.
- After `cloudsql`, copy the printed `DATABASE_URL` into `.env` and start the Cloud SQL Auth Proxy on `127.0.0.1:5434`. Re-running `provision.py cloudsql` is idempotent: `_ensure_user` preserves an existing user's password rather than rotating it unexpectedly.

## Why resources stay up

These tutorials are designed for shared sandbox use. Creating a Cloud SQL instance or Bigtable-backed Feature Online Store per run takes minutes and requires admin IAM roles. One team member provisions shared resources once so developers only need runtime "use" roles. Ongoing cost comes primarily from Cloud SQL (`db-f1-micro`) and Tutorial 15's single Bigtable node; pause the SQL instance (`gcloud sql instances patch agentops-tutorial --activation-policy NEVER`) and delete the Feature Online Store when idle.
