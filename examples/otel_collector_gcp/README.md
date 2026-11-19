# Example OTel Collector -> Google Cloud Trace (`v1.0.0`)

Demonstrates running a local OpenTelemetry Collector with Grafana/Prometheus and exporting traces from an instrumented Python script (`sample_agent_tracing.py`) to Google Cloud Trace via Application Default Credentials (ADC) or OTLP gRPC.

`sample_agent_tracing.py` uses `agentops-sdk` v1.0.0 (`configure_telemetry(mode="otlp", ...)`, `bind_execution_scope`, `@trace_agent_flow`, `@trace_agent_span`, and `@trace_llm_call`) and cleanly flushes and shuts down providers at exit.

---

## Prerequisites

1. `gcloud` CLI and Docker / Docker Compose installed.
2. Python 3.11+ with `agentops-sdk` installed from the repository root (`pip install -e ".[all]"`).
3. Valid GCP ADC credentials on disk (by default `$HOME/.config/gcloud/application_default_credentials.json`).

---

## Authentication

Authenticate with `gcloud` and set your quota project (or impersonate a service account with `roles/cloudtrace.agent` and `roles/iam.serviceAccountTokenCreator`):

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project <your-project-id>
gcloud auth application-default set-quota-project <your-project-id>
```

Optionally point `KEY_FILE` to a custom credentials JSON file; otherwise `docker-compose.yaml` mounts `$HOME/.config/gcloud/application_default_credentials.json` read-only into `/etc/gcp/sa-key.json`.

---

## Execution

```bash
export GOOGLE_CLOUD_PROJECT="<your-project-id>"
export GF_SECURITY_ADMIN_PASSWORD="<your-strong-local-grafana-password>"
# Optional custom ADC path:
# export KEY_FILE="$HOME/.config/gcloud/application_default_credentials.json"

# 1. Start the collector, Prometheus, and Grafana stack
cd examples/otel_collector_gcp
docker compose up -d

# 2. Inspect collector logs
docker compose logs -f

# 3. Run the v1.0.0 sample tracing script
cd ../..
python examples/otel_collector_gcp/sample_agent_tracing.py
```

Check spans in Cloud Trace inside the GCP Console or query local metrics/dashboards via `otel-collector-config.yaml`.

> [!NOTE]
> Stop this stack (`docker compose down`) before starting [`../full_stack`](../full_stack) because both bind ports `4317`, `9090`, and `3000`.

## License

```
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
```
