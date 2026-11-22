# Observability & FinOps Telemetry (`v1.0.0`)

## Unified OpenTelemetry Provider

`configure_telemetry(mode=...)` (`sdk/src/agentops_sdk/telemetry/provider.py`) supports:
- `gcp`: Cloud Trace, Cloud Monitoring, and Cloud Logging.
- `otlp`: Local/remote OTLP gRPC (`Traces`, `Metrics`, and `OtlpStdlibLogHandler` structured logs) with zero Cloud Logging leakage.
- `hybrid`: Both GCP and OTLP simultaneously.
- `auto` / `off`: Auto-detect or disable exporters.

## Instrumentation & Per-Case Telemetry

- **Decorators**: `@trace_agent_flow`, `@trace_agent_span`, `@trace_llm_call`, `@trace_use_case`.
- **FinOps & KPIs**: `log_llm_metrics`, `emit_finops_telemetry`, `UseCaseTracker`, and `record_fallback_metric()`.
- **Log Router CLI**: `python -m tools.observability.log_router_admin` (`setup --dry-run`, `export --destination bq|gcs|file|both --output-file ...`, `list-sinks`, `verify`).

## Consolidated Full-Stack Demo

Run the complete local Grafana/Alloy/Tempo/Prometheus/Loki stack or zero-Docker hermetic mode:

```bash
MODE=hermetic python examples/full_stack/run_full_stack.py --hermetic
```

See [docs/universal_trace_and_finops_guide.md](../docs/universal_trace_and_finops_guide.md) and [examples/full_stack/README.md](../examples/full_stack/README.md).
