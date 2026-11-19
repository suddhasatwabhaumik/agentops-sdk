<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# CI/CD & Monitoring Integration (`v1.0.0`)

This document outlines the CI/CD quality gate architecture, build pipelines, and FinOps/observability telemetry pipeline configured for AgentOps (`v1.0.0`).

---

## 1. Cloud FinOps & Observability Pipeline

`agentops_sdk.telemetry` captures token consumption, stage timing, and operational metrics and correlates costs directly to business KPIs, agent projects, and environments.

### OpenTelemetry Span Correlation
`configure_telemetry(mode=...)` (`auto`, `gcp`, `otlp`, `hybrid`, `off`) initializes `TracerProvider`, `MeterProvider`, and `OtlpStdlibLogHandler` bridging. `@trace_agent_flow`, `@trace_agent_span`, and `@trace_llm_call` emit standardized attributes across spans and structured FinOps logs:
*   `business.kpi_id`
*   `business.pl_impact_line`
*   `agentops.project.name`
*   `agentops.environment`
*   `agentops.agent.role` / `agentops.agent.name` / `agentops.agent.urn`
*   `agentops.session.id` / `agentops.user.id`
*   `llm.model_name` / `llm.usage.prompt_tokens` / `llm.usage.completion_tokens` / `gen_ai.*`

### Logging & BigQuery Log Sink
1.  **OTel Tracer & Meter**: Traces export to Google Cloud Trace (`CloudTraceSpanExporter`) and/or an OTLP Collector (`BatchSpanProcessor`); token metrics emit to `gen_ai.client.token.usage`.
2.  **Single-Stream Cloud Logging Defaults (`ADR-2`)**: FinOps logs (`agentops-finops-telemetry`), per-case telemetry (`UseCaseTracker` -> `agentops-use-case-telemetry`), and compliance audits (`AuditLedger` -> `agentops-audit-ledger`) write cleanly to Cloud Logging by default.
3.  **Log Router Sinks (`tools/observability/`)**: `python -m tools.observability.log_router_admin` and [`tools/observability/setup_log_routers.sh`](../tools/observability/setup_log_routers.sh) provision partitioned BigQuery (`agentops_observability`) and GCS archive sinks asynchronously.
4.  **Cost Correlation Join**: In BigQuery, exported telemetry rows join with live GCP Billing exports (`project.id`, `agentops.project.name`, `business.kpi_id`) for granular FinOps attribution without hardcoding token pricing inside SDK code.
5.  **Looker Studio Dashboard**: Surfaces token spend, latency percentiles, and cost per business KPI.

See [`universal_trace_and_finops_guide.md`](universal_trace_and_finops_guide.md) and [`bigquery_gcs_observability_and_case_telemetry_guide.md`](bigquery_gcs_observability_and_case_telemetry_guide.md) for full schemas.

---

## 2. CI/CD Quality Gates (`agentops_eval` Pytest Plugin)

To block hallucinations, relevance regressions, and multi-turn tool trajectory failures prior to release, `agentops-sdk` registers the `agentops_eval` pytest plugin (`agentops_sdk.pytest_plugin`) in root [`pyproject.toml`](../pyproject.toml):

1.  **Pull Request Gate (`--eval-mode=in_memory` or `heuristic`)**: Runs fast hermetic assertions (`assert_quality_gate`, `assert_trajectory_quality_gate`) in seconds with zero GCP network calls.
2.  **Main / Nightly Gate (`--eval-mode=vertex`)**: Runs live **Vertex AI Evaluation Service** (`VertexEvalTaskAdapter`) scoring against golden datasets (`tests/data/golden_eval_dataset.yaml`):
    *   **Groundedness** (default `--fail-under-groundedness`: `0.85`)
    *   **QA Relevance** (default `--fail-under-relevance`: `0.80`)
    *   **Tool Use Quality** (default `--fail-under-tool-quality`: `0.80`)
    *   **Task Success** (default `--fail-under-task-success`: `0.80`)
3.  **Failure & Summary Behavior**: Any threshold violation raises `QualityGateError`, failing CI; `pytest_sessionfinish` writes a sanitized Markdown summary (`--eval-report-path=eval_quality_gate_summary.md`) suitable for `$GITHUB_STEP_SUMMARY`.

See [`cicd_quality_gates_pytest_integration.md`](cicd_quality_gates_pytest_integration.md) and [`vertex_ai_evaluator_guide.md`](vertex_ai_evaluator_guide.md) for details.

---

## 3. Repository CI/CD Workflows & Templates

### A. Repository CI, Wheel Packaging & GitHub Release ([`.github/workflows/build-wheel.yml`](../.github/workflows/build-wheel.yml))
Triggered on pushes and PRs to `main` (plus `workflow_dispatch`):
*   **Lint & Type Checks**: Runs `ruff check`, `ruff format --check` across `sdk/src/`, `mcp_servers/src/`, `tests/`, `tools/`, `examples/`, and `mypy --strict` across `sdk/src/agentops_sdk` and `mcp_servers/src`.
*   **Unit & Contract Suite**: Runs `pytest -v tests/unit tests/contract --eval-mode=in_memory --eval-report-path=eval_quality_gate_summary.md` with coverage reporting.
*   **Release Notes & Wheel Integrity**: Runs `python -m tools.release_notes.consolidate`, builds `.whl` from repository root via `python -m build --wheel .`, verifies `agentops_sdk/py.typed` inside the wheel, runs `twine check`, smoke-tests base and `[all]` extras in isolated virtualenvs, and generates `SHA256SUMS`.

### B. Downstream Agent Quality Gate Template ([`examples/cicd_templates/agent_quality_gates.yml`](../examples/cicd_templates/agent_quality_gates.yml))
Reusable two-tier GitHub Actions template for consumer agent repositories:
*   **PR Tier**: `pip install -e ".[eval,dev]"` followed by `pytest -v -m quality_gate ... --eval-mode=in_memory`.
*   **Main / Scheduled Tier**: Authenticates via Google Cloud Workload Identity Federation (`google-github-actions/auth@v2`) and executes `--eval-mode=vertex`.

### C. Google Cloud Build Pipeline ([`cloudbuild.yaml`](../cloudbuild.yaml))
1.  **Install & Lint**: Installs `.[dev,all]` and enforces `ruff check`, `ruff format --check`, and `mypy --strict`.
2.  **OPA Policy Check**: Executes pinned `openpolicyagent/opa:0.70.0-static`.
3.  **Unit & Contract Tests**: Executes `pytest -v tests/unit tests/contract --eval-mode=in_memory`.
4.  **Build & Conditional Artifact Registry Upload**: Builds via `python3 -m build .`, validates via `twine check dist/*`, and publishes via `twine upload` to `https://${_GCP_REGION}-python.pkg.dev/${PROJECT_ID}/${_AR_REPOSITORY}/` when a release tag (`_TAG_NAME` / `TAG_NAME`) is present.
