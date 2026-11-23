# 01 · Tracing and FinOps: One Agent Flow, One Real Gemini Call (`v1.0.0`)

**What you learn:** how `bind_execution_scope` and three telemetry decorators turn a plain Python function into a hierarchically traced agent flow whose LLM token usage and cost metadata correlate cleanly by business KPI.

```
bind_execution_scope(session_id, user_id)
  └─ tenant_parts_advisor()  ── @trace_agent_flow ──► span  agent_flow:tenant_parts_advisor
       ├─ lookup_stock()     ── @trace_agent_span ──► span  agent_run:stock_lookup (child)
       └─ ask_gemini()       ── @trace_llm_call  ───► span  llm_call:ask_gemini + Vertex AI Gemini
                                                       └─ FinOps log record {model, tokens, latency,
                                                                             kpi_id, pl_impact_line, trace_id}
                                           │
                               OTLP gRPC   ▼  localhost:4317 (Alloy via configure_telemetry(mode="otlp"))
                    Tempo (spans) · Loki (FinOps logs) · Prometheus (span metrics) · Grafana :3002
```

## SDK calls used

| Call | What it does |
|---|---|
| `configure_telemetry(mode="otlp", ...)` | Configures OTLP trace, metric, and native `OtlpStdlibLogHandler` log forwarding locally (zero Cloud Trace / Cloud Logging writes) |
| `bind_execution_scope(session_id=..., user_id=...)` | Binds caller `session_id` and `user_id` into async-safe `ContextVar` scope |
| `@trace_agent_flow(flow_name, agent_role, kpi_id, pl_impact_line, ...)` | Opens the root flow span (`agent_flow:<name>`) tagged with FinOps metadata |
| `@trace_agent_span(agent_name=...)` | Opens a child span (`agent_run:<agent_name>`) for a tool or sub-agent |
| `@trace_llm_call(...)` | Wraps the LLM call, extracts `usage_metadata` from the returned Gemini response, calls `log_llm_metrics()`, and emits the structured FinOps log record |
| `log_llm_metrics(*, model_name, input_tokens, output_tokens, cached_tokens=0)` | Records `llm.*` and `gen_ai.*` token attributes and `gen_ai.client.token.usage` metrics on the active span |

Return the full Gemini response object (not `response.text`) from a `@trace_llm_call` function so the decorator can read `usage_metadata` and record exact token counts instead of `model_name="unknown", tokens=0`.

## GCP resources used

| Resource | Why | Access needed |
|---|---|---|
| Vertex AI, Gemini (`GEMINI_MODEL`, `GEMINI_LOCATION`) | Live LLM inference | `roles/aiplatform.user` on the project |
| Application Default Credentials | Identity for Vertex AI calls | `gcloud auth application-default login` |

Because [`_shared/telemetry.py`](../_shared/telemetry.py) selects `mode="otlp"`, nothing is written to Google Cloud Trace or Cloud Logging.

## Run

```bash
bash examples/tutorial/observability/stack.sh up      # once
python examples/tutorial/01_tracing_and_finops/main.py
```

Expected output ends with `arrived in Tempo: True` and a `trace_id`. Open Grafana → Explore → Tempo and search that `trace_id` to inspect the three nested spans (`llm.usage.*` and `gen_ai.*` attributes on `llm_call:ask_gemini`), and search Loki for the correlated FinOps log record carrying `llm.input_tokens`, `llm.output_tokens`, and `business.kpi_id`.

## What to inspect in Grafana

Dashboard **AgentOps SDK - Overview** → **LLM FinOps and quality**: filter `instance = <run_id printed by the script>`.
