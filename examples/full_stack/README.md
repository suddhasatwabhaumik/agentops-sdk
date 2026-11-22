# Full-stack telemetry example (`v1.0.0`)

A local Grafana stack (Alloy, OpenTelemetry Collector, Prometheus, Tempo, Loki, Grafana) and an end-to-end validation harness that exercises every telemetry-emitting component of `agentops-sdk` v1.0.0, then verifies that every span, metric, and log record landed where expected. No GCP project or credentials are required: external services are faked and GCP adapters are cleanly substituted with built-in SDK test doubles (`LocalRuleSafetyScanner`, `RegexPrivacyScrubber`, `InMemorySessionMemory`, `InMemoryAuditAdapter`, `InMemoryTelemetryExporter`, `InMemoryEvaluator`).

```
                          ┌─► Alloy          :4317  ─┬─ metrics ─► Prometheus (remote write)
run_full_stack.py ─OTLP ──┤                          ├─ traces ──► Tempo ─ span metrics ─► Prometheus
  (AgentOps SDK)          └─► OTel Collector :14317 ─┤   metrics ─► :8889 ◄─ scraped by Prometheus
                                                     └─ logs ────► Loki (native OTLP)
                                  Grafana :3000 ◄── Prometheus · Tempo · Loki (cross-linked)
```

Both pipelines deliver the same three signals to the same backends. Each pipeline stamps `ingest_path="alloy"` or `ingest_path="otel-collector"` onto every span, datapoint, and log record so signals are isolated by path.

## Run it

Prerequisites: Docker (or Podman) with Compose, and Python 3.11+ with the SDK installed from the repository root (`pip install -e ".[all]"`).

```bash
# 1. Start the observability stack
cd examples/full_stack
docker compose up -d

# 2. From the repository root: emit through Alloy, then validate
cd ../..
python examples/full_stack/run_full_stack.py

# Through the OTel Collector instead, or through both one after the other
python examples/full_stack/run_full_stack.py --target otel-collector
python examples/full_stack/run_full_stack.py --target both

# Keep traffic flowing for 10 minutes so the dashboards have continuous data
python examples/full_stack/run_full_stack.py --loop 600

# Run hermetic mode with zero Docker or collector required (validates SDK assertions locally)
MODE=hermetic python examples/full_stack/run_full_stack.py --hermetic
```

`--iterations N` sets the number of scenario rounds (default `3`), `--hermetic` (or `MODE=hermetic`) runs locally with `configure_telemetry(mode="off")` and skips collector/backend polling, `--no-validate` emits without validating, `--verbose` prints SDK logs, and `--timeout` sets how long the validator waits for data to arrive (default `90` s). The exit code is non-zero if any check fails, so the script can gate CI.

Stop and discard all volumes with `docker compose down -v`.

## What the script exercises

Each round runs six requests through an enterprise parts advisor built with SDK decorators, plus a rate-limit burst and a prompt lookup exercising adapter fallback via `PromptRegistry(adapter=..., fallback_adapter=...)`:

| Scenario | SDK surface exercised | Signal |
|---|---|---|
| Answered, first tool attempt returns bad data | `configure_telemetry(mode="otlp")`, `bind_execution_scope`, `@trace_agent_flow`, `@trace_agent_span`, `@validate_tool_output` retry, `@rate_limit`, `apply_row_level_policy`, `@validate_sql_query_decorator`, `PromptRegistry.render_prompt`, `@trace_llm_call`, `log_llm_metrics`, `OutputDLPSanitizer`, `evaluate_agent_response`, `VertexEvalTaskAdapter` (`gemini-3.8-flash` `raw_scores`/`explanations`/`console_url`), `CompositeMemory` (`memory_type="preference"` filtering + consolidation), `AuditLedger(sink=InMemoryAuditAdapter())`, `CompositeTelemetryExporter` | spans, span events, metrics, FinOps log |
| Answered, response contains email and phone | `OutputDLPSanitizer(scrubber=RegexPrivacyScrubber())` (`agentops.privacy.pii_scrubbed=true`) | span attributes |
| Prompt injection | `SecurityGuard(scanner=LocalRuleSafetyScanner(), fail_closed=True)` | span (`safety.is_safe=false`) |
| LLM-generated SQL reads `finance.credit_limits` | `@validate_sql_query_decorator` allowlist | error span (`sql.ast.is_safe=false`) |
| Tool keeps returning invalid data | `@validate_tool_output` exhausts retries, `ToolValidationError` | error span, retry events |
| LLM times out | `@trace_llm_call` error path | error span, FinOps log `llm.status=ERROR` |
| Burst of 10 bulk calls | `@rate_limit` concurrency and sliding-window rejections | `agentops.rate_limit.*` metrics |
| Primary prompt backend failure | `PromptRegistry(adapter=..., fallback_adapter=...)` | `agentops.prompt.fallbacks.total` |

Identity claims are generated with `JWTAuthEngine` (`create_access_token` / `decode_token`) and bound into `ExecutionScope` via `bind_execution_scope(session_id=..., user_id=..., claims=...)`.

## What the validator proves

Every check is scoped to the run's `service.instance.id` and selected `ingest_path`, comparing against exact counts recorded by the workload:

- **Prometheus:** All six SDK metric instruments exist (`agentops_rate_limit_*` and `agentops_prompt_*`). Request and rejection counters match exact tallies, and the concurrency gauge returns to `0`.
- **Tempo:** Every trace ID produced is retrievable. Expected span names (`agent_flow:*`, `agent_run:inventory_agent`, `agent_run:answer_agent`, `agent_run:price_sync_agent`, `check_content_safety`), span attributes (`business.kpi_id`, `agentops.agent.urn`, `safety.is_safe`, `sql.ast.is_safe`, `tool.validation.status`, `tool.validation.error`, `llm.usage.prompt_tokens`, `agentops.privacy.pii_scrubbed`, `ingest_path`), retry events, error statuses, and TraceQL queries all validate.
- **Tempo span metrics in Prometheus:** A series exists for each SDK span name.
- **Loki:** One FinOps record per LLM call with exact token totals and matching `trace_id` join keys, plus one evaluation record per answered flow.
- **Pipeline & Grafana** (advisory): Receivers accept all three signals cleanly and Grafana datasources/dashboards report healthy.

## Endpoints & Dashboards

| Endpoint | URL |
|---|---|
| Grafana (anonymous `Viewer` role) | http://localhost:3000 |
| Alloy UI | http://localhost:12345 |
| Prometheus | http://localhost:9090 |
| Tempo API | http://localhost:3200 |
| Loki API | http://localhost:3100 |
| OTLP into Alloy (gRPC / HTTP) | `localhost:4317` / `localhost:4318` |
| OTLP into OTel Collector (gRPC / HTTP) | `localhost:14317` / `localhost:14318` |

Provisioned dashboards in **AgentOps SDK**:
- **AgentOps SDK - Overview:** Agent flows and guardrails, rate limiting and prompt registry metrics, LLM tokens/latency/eval quality, and trace-to-log cross-links (`trace_id`).
- **AgentOps - Telemetry Pipeline:** Receiver/exporter throughput, remote write, and backend ingestion metrics.

## Native OTLP & Collector Behavior Notes (`v1.0.0`)

- **Native OTLP Log Bridging (`OtlpStdlibLogHandler`):** `configure_telemetry(mode="otlp")` natively attaches `OtlpStdlibLogHandler` to forward `agentops_sdk*` / `agentops.*` structured logs (`json_fields`) over OTLP to Loki with zero local adapter monkey-patching.
- **Zero Cloud Logging Leakage in OTLP Mode:** `@trace_llm_call` only ships FinOps records to Google Cloud Logging when the active telemetry mode is `gcp` or `hybrid`. Local `otlp` runs stay 100% local even when ADC credentials exist on disk.
- **Native Adapter Injection:** `PromptRegistry` natively accepts `adapter=` and `fallback_adapter=` keyword arguments, and `CompositeTelemetryExporter` fans out to `[InMemoryTelemetryExporter(), LocalFileTelemetryExporter(...)]`.
- **Fast Metric Flush:** `run_full_stack.py` sets `OTEL_METRIC_EXPORT_INTERVAL=5000` before calling `configure_telemetry(mode="otlp", ...)` so Prometheus receives points within 5 seconds instead of waiting 60 seconds.
- **Error-Path Model Attribution:** When a `@trace_llm_call` function raises before returning a response dict, the error FinOps record reports `llm.model_name="unknown"` unless `model_name_override` is specified.
- **Tempo Span Metrics Dimensioning:** Tempo's span-metrics generator omits `service.instance.id`, so span-metric panels reflect cumulative local stack history rather than a single run filter.

OTLP to Prometheus metric translation:

| OpenTelemetry instrument | Prometheus series |
|---|---|
| `agentops.rate_limit.requests.total` | `agentops_rate_limit_requests_total` |
| `agentops.rate_limit.rejected.total` | `agentops_rate_limit_rejected_total` |
| `agentops.rate_limit.concurrency.active` | `agentops_rate_limit_concurrency_active` |
| `agentops.prompt.loads.total` | `agentops_prompt_loads_total` |
| `agentops.prompt.renders.total` | `agentops_prompt_renders_total` |
| `agentops.prompt.fallbacks.total` | `agentops_prompt_fallbacks_total` |

## Version notes

Pinned stack images: Alloy v1.20.1, OTel Collector contrib 0.161.0, Tempo 3.1.0, Loki 3.7.8, Prometheus v3.15.0, Grafana 13.2.3.

- **Collector contrib 0.161** uses `otlp_grpc` / `otlp_http` exporter names.
- **Alloy** appends `_total` to its OTel self-metrics whereas the OTel Collector does not (`otelcol_receiver_accepted_spans_total` vs `otelcol_receiver_accepted_spans`).
- **Tempo 3** runs single-binary `-target=all` with `query_frontend.query_end_cutoff` lowered to `5s`.
