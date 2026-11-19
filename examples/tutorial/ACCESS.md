# Access to Request for the Tutorials (`v1.0.0`)

Project `<your-gcp-project-id>`, region `europe-west4`. Run `examples/tutorial/00_setup/preflight.py` to check which permissions, APIs, and shared resources are active for your identity.

## Standard Baseline Roles

With `roles/aiplatform.user` and Secret Manager access (`roles/secretmanager.secretAccessor`, or admin roles in sandbox), tutorials **01, 03, 04, 07, 08, 11, and 13** run immediately; **05** and **06** run once the `agentops_tutorial` BigQuery dataset and `dealer_stock` table exist.

## Additional Access Matrix

| # | Need | Unblocks | SDK Class / Component | Smallest Grant |
|---|---|---|---|---|
| 1 | Create & write BigQuery tables | `02`, `14`, and provisioning `agentops_tutorial` (`05`, `06`, `15`) | `BigQueryTelemetryExporter`, `BigQueryAuditAdapter`, `00_setup/provision.py bigquery`, `FeatureStoreAdminClient` | `roles/bigquery.dataEditor` on datasets `agentops_observability`, `agentops_governance`, and `agentops_tutorial` (plus `roles/bigquery.jobUser` for provisioning/queries) |
| 2 | Call centrally provisioned Model Armor templates (`agentops-input-guard`, `agentops-output-guard`) | `09` | `SecurityGuard` + `CloudModelArmorScanner` (templates managed via central infrastructure IaC) | `roles/modelarmor.user` and `roles/modelarmor.viewer` |
| 3 | Enable `dlp.googleapis.com` and `sqladmin.googleapis.com` | `10`, `12` | `00_setup/provision.py apis` | `roles/serviceusage.serviceUsageAdmin` once (or project-owner API enablement) |
| 4 | Call regional Cloud DLP | `10` | `OutputDLPSanitizer` + `GoogleDLPScrubber` | `roles/dlp.user` |
| 5 | Provision & connect to Cloud SQL (Postgres) | `12` | `00_setup/provision.py cloudsql`, `CloudSQLSessionMemory` | `roles/cloudsql.admin` once for setup; `roles/cloudsql.client` for developers (with Cloud SQL Auth Proxy) |

You can either grant items 1–5 to developers directly, or impersonate the pre-configured runtime service account `<agent-runner-sa>@<your-gcp-project-id>.iam.gserviceaccount.com` using `roles/iam.serviceAccountTokenCreator`:

```bash
gcloud auth application-default login --impersonate-service-account \
  <agent-runner-sa>@<your-gcp-project-id>.iam.gserviceaccount.com
```

## Intentionally Not Required

- `roles/cloudtrace.agent`, `roles/logging.logWriter`: tutorials configure `configure_telemetry(mode="otlp", ...)` so traces, metrics, and logs flow only to the local OTLP endpoint in `OTEL_COLLECTOR_URL`. (Tutorial `02` also sends explicit case telemetry via `CompositeTelemetryExporter([CloudLoggingTelemetryExporter(...), BigQueryTelemetryExporter(...)])`, which uses Cloud Logging write access if tested with the GCP Cloud Logging exporter enabled.)
- Exported Service Account JSON key files: everything runs via Application Default Credentials or service-account impersonation.

## Verify After a Grant

```bash
python examples/tutorial/00_setup/preflight.py
python examples/tutorial/00_setup/provision.py all
```
