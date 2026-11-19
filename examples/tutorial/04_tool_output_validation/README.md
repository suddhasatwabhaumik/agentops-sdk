# 04 · Tool Output Validation: Typed Contracts Between Steps (`v1.0.0`)

**What you learn:** how to enforce Pydantic output schemas and domain assertion callbacks on tools and LLM JSON responses with automatic backoff retries and OpenTelemetry retry events.

```
tool() ──► result ──► StockReport(**result) + assertion_callback ── ok ──► return validated dict/model
                            │ invalid
                            └──► span event "tool.validation.retry" ──► retry tool()
                                                   │ after max_retries
                                                   └──► raises ToolValidationError
```

## SDK calls used

| Call | What it does |
|---|---|
| `@validate_tool_output(schema_model=..., assertion_callback=..., max_retries=..., retry_delay_seconds=..., backoff_factor=..., jitter_seconds=...)` | Validates tool outputs (sync or async) against a Pydantic model and/or assertion predicate, retrying failed attempts automatically |
| `ToolValidationError` | Raised when all attempts fail schema or assertion checks (carries `.tool_name`, `.attempts`, `.last_error`) |

Three scenarios in `main.py`:

1. **Flaky tool:** First return sets `units_available=-1` (violating `ge=0`); second attempt returns valid stock and passes.
2. **LLM structured JSON + business assertion:** Gemini produces JSON matching `response_schema=StockReport`, and `@validate_tool_output` additionally verifies the semantic assertion (`must_mention_warehouse`).
3. **Always invalid:** Exhausts `max_retries` and raises `ToolValidationError`, failing loudly with span attribute `tool.validation.status="FAIL"`.

## GCP resources used

| Resource | Why | Access needed |
|---|---|---|
| Vertex AI, Gemini | Structured-output JSON generation in scenario 2 | `roles/aiplatform.user` |

## Run

```bash
python examples/tutorial/04_tool_output_validation/main.py
```

Expected: scenario 1 passes after 2 attempts, scenario 2 prints a validated `StockReport` dict, and scenario 3 raises `ToolValidationError`. Inspect the trace in Tempo to view `tool.validation.retry` span events and `tool.validation.attempts`.
