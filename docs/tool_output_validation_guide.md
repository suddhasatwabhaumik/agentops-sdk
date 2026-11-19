<!--
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
-->

# Tool Output Validation Guide (`@validate_tool_output`, `v1.0.0`)

## 1. Executive Summary

AI Agents in enterprise supply-chain and data science environments frequently invoke external tools, query engines (e.g., Google BigQuery, Vertex AI Feature Store), and downstream microservices. When language models synthesize tool parameters or parse downstream tool returns, multiple operational failure modes occur:
1. **Schema Mismatches & Type Hallucinations**: Downstream APIs or LLM function-calling wrappers may return unexpected types (e.g., strings instead of floats, missing required fields, or raw unparsed JSON strings).
2. **Transient Network or Upstream Flakiness**: Temporary timeouts, rate limits, or microservice hiccups cause immediate tool execution aborts without retry resilience.
3. **Data Integrity & Security Invariant Violations**: Outputs may parse as JSON but violate domain constraints or security boundaries (e.g., negative inventory balances or leaky sensitive tokens).
4. **Lack of Observability**: Engineers need span-level visibility into validation attempts, backoff retries, and scrubbed failure causes.

To solve these challenges, `agentops_sdk.resilience` provides **`@validate_tool_output`** ([`sdk/src/agentops_sdk/resilience/tool_validator.py`](../sdk/src/agentops_sdk/resilience/tool_validator.py)).

---

## 2. Architecture Overview

`@validate_tool_output` wraps synchronous (`def`) and asynchronous (`async def`) tool functions with schema validation, custom assertions, bounded exponential backoff with jitter, exception filtering (`retry_on_exceptions`), and OTel span telemetry:

```mermaid
flowchart TD
    A["Agent / Tool Invocation"] --> B{"Is Coroutine (async def)?"}

    subgraph Async_Execution["Async Pipeline"]
        B -->|Yes| C["Execute: await func(*args, **kwargs)"]
        C -->|Exception in retry_on_exceptions| G
        C -->|Result returned| D["_check_async_output"]
        D --> E{"Pydantic Schema Check"}
        E -->|Valid| F{"Assertion Callback?"}
        E -->|Invalid| G["Record Retryable Failure"]
        F -->|Yes (async or sync)| H["Await / Execute Callback"]
        F -->|None| I["Validation Success"]
        H -->|Returned False or Raised| G
        H -->|Returned True| I
    end

    subgraph Sync_Execution["Sync Pipeline"]
        B -->|No| J["Execute: func(*args, **kwargs)"]
        J -->|Exception in retry_on_exceptions| G
        J -->|Result returned| K["_check_sync_output"]
        K --> L{"Pydantic Schema Check"}
        L -->|Valid| M{"Assertion Callback?"}
        L -->|Invalid| G
        M -->|Sync Function| N["Execute Callback"]
        M -->|Async Coroutine| O["Close Coroutine & Raise TypeError"]
        M -->|None| I
        N -->|Returned False or Raised| G
        N -->|Returned True| I
    end

    G --> P{"attempt < max(1, max_retries)?"}
    P -->|Yes| Q["Record OTel Event: tool.validation.retry"]
    Q --> R["Sleep (delay * backoff_factor^(attempt-1) + jitter)"]
    R --> S["Next Attempt"]
    S --> B
    P -->|No| T["Stamp OTel Span: tool.validation.status = FAIL"]
    T --> U["Raise ToolValidationError(tool_name, attempts, last_error)"]

    I --> V["Stamp OTel Span: tool.validation.status = PASS, attempts = n"]
    V --> W["Return Validated Result"]
```

---

## 3. Decorator API Specification

```python
from collections.abc import Awaitable, Callable
from typing import Any

import pydantic

from agentops_sdk.resilience import validate_tool_output


def example_signature(
    *,
    schema_model: type[pydantic.BaseModel] | None = None,
    assertion_callback: Callable[[Any], bool | Awaitable[bool]] | None = None,
    max_retries: int = 3,
    retry_delay_seconds: float = 0.0,
    backoff_factor: float = 2.0,
    jitter_seconds: float = 0.0,
    retry_on_exceptions: tuple[type[BaseException], ...] = (),
):
    return validate_tool_output(
        schema_model=schema_model,
        assertion_callback=assertion_callback,
        max_retries=max_retries,
        retry_delay_seconds=retry_delay_seconds,
        backoff_factor=backoff_factor,
        jitter_seconds=jitter_seconds,
        retry_on_exceptions=retry_on_exceptions,
    )
```

### Parameter Reference (Keyword-Only)

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `schema_model` | `type[BaseModel] \| None` | `None` | Pydantic v2 model enforcing structural schema, type coercion, and field validations. |
| `assertion_callback` | `Callable[[Any], bool \| Awaitable[bool]] \| None` | `None` | Custom function performing semantic integrity checks. Returning `False` or raising triggers retry. |
| `max_retries` | `int` | `3` | Maximum total execution attempts (`max(1, max_retries)`). |
| `retry_delay_seconds` | `float` | `0.0` | Base delay in seconds before the first retry attempt. |
| `backoff_factor` | `float` | `2.0` | Exponential multiplier between retries ($\text{delay}_k = \text{retry\_delay\_seconds} \times \text{backoff\_factor}^{k-1} + \text{jitter}$). |
| `jitter_seconds` | `float` | `0.0` | Uniform random jitter upper bound in seconds (`[0.0, jitter_seconds]`). |
| `retry_on_exceptions` | `tuple[type[BaseException], ...]` | `()` | Optional tuple of exception types raised by `func` itself that should trigger retry (all other exceptions from `func` propagate immediately). |

---

## 4. Supported Return Types & Collections

`@validate_tool_output` validates multiple output shapes while preserving the tool's original return object:

### 4.1 Pydantic Model Instance
```python
from pydantic import BaseModel

from agentops_sdk.resilience import validate_tool_output


class MyModel(BaseModel):
    field_a: str
    score: float


@validate_tool_output(schema_model=MyModel)
def get_data() -> MyModel:
    return MyModel(field_a="value", score=0.95)
```

### 4.2 Dictionary (`dict`)
```python
from pydantic import BaseModel

from agentops_sdk.resilience import validate_tool_output


class MyModel(BaseModel):
    field_a: str
    score: float


@validate_tool_output(schema_model=MyModel)
def get_data_dict() -> dict[str, object]:
    return {"field_a": "value", "score": 0.95}
```

### 4.3 List & Tuple Collections (`list[dict]`, `tuple[dict, ...]`)
```python
from pydantic import BaseModel

from agentops_sdk.resilience import validate_tool_output


class MyModel(BaseModel):
    field_a: str
    score: float


@validate_tool_output(schema_model=MyModel)
def get_batch() -> tuple[dict[str, object], ...]:
    return (
        {"field_a": "val1", "score": 0.8},
        {"field_a": "val2", "score": 0.9},
    )
```

### 4.4 Serialized JSON String (`str` / `bytes` / `bytearray`)
```python
from pydantic import BaseModel

from agentops_sdk.resilience import validate_tool_output


class MyModel(BaseModel):
    field_a: str
    score: float


@validate_tool_output(schema_model=MyModel)
def get_raw_json() -> str:
    return '{"field_a": "value", "score": 0.95}'
```

---

## 5. Custom Assertion Callbacks

Assertion callbacks enforce semantic invariants that go beyond structural schemas (e.g., token leakage checks or cross-field checks).

### Synchronous Assertion Callback
```python
from typing import Any

from agentops_sdk.resilience import validate_tool_output


def verify_no_sensitive_tokens(result: Any) -> bool:
    content = str(result).lower()
    return "bearer" not in content and "api_key" not in content


@validate_tool_output(assertion_callback=verify_no_sensitive_tokens, max_retries=3)
def fetch_public_profile() -> dict[str, str]:
    return {"user": "alice", "role": "engineer"}
```

### Asynchronous Assertion Callback
Asynchronous assertion callbacks (`async def`) are supported with `async def` tools and awaited cleanly:
```python
import asyncio
from typing import Any

from agentops_sdk.resilience import validate_tool_output


async def async_verify_remote_quota(result: Any) -> bool:
    await asyncio.sleep(0.01)
    return int(result.get("quota_remaining", 0)) > 0


@validate_tool_output(assertion_callback=async_verify_remote_quota, max_retries=3)
async def invoke_agent_tool() -> dict[str, object]:
    return {"status": "ok", "quota_remaining": 15}
```

> [!NOTE]
> If an `async def` `assertion_callback` is attached to a synchronous tool function, `@validate_tool_output` closes the unawaited coroutine cleanly and raises `TypeError("Async assertion_callback cannot be used with a sync tool.")`.

---

## 6. Retry & Exponential Backoff

When validation fails (or an exception listed in `retry_on_exceptions` occurs), `@validate_tool_output` sleeps between attempts according to:

$$\text{delay}_k = \text{retry\_delay\_seconds} \times (\text{backoff\_factor})^{k - 1} + \text{random}(0, \text{jitter\_seconds}) \quad \text{for attempt } k = 1, \dots, \text{max\_retries} - 1$$

Example with `retry_delay_seconds = 0.1`, `backoff_factor = 2.0`, `jitter_seconds = 0.0`, and `max_retries = 4`:

| Attempt | Sleep Before Next Attempt | Action Upon Validation Failure |
| :--- | :--- | :--- |
| Attempt 1 | `0.10s` | Record `tool.validation.retry` span event, sleep `0.10s` |
| Attempt 2 | `0.20s` | Record `tool.validation.retry` span event, sleep `0.20s` |
| Attempt 3 | `0.40s` | Record `tool.validation.retry` span event, sleep `0.40s` |
| Attempt 4 | None (exhausted) | Stamp `tool.validation.status = "FAIL"`, raise `ToolValidationError` |

---

## 7. Exception Handling (`ToolValidationError`)

When all attempts are exhausted, `@validate_tool_output` raises `agentops_sdk.domain.exceptions.ToolValidationError` (also exported at `agentops_sdk.ToolValidationError`):

```python
import logging

from agentops_sdk.domain.exceptions import ToolValidationError

logger = logging.getLogger(__name__)


def run_with_error_handling(execute_analytical_tool) -> object | None:
    try:
        return execute_analytical_tool()
    except ToolValidationError as err:
        logger.error(
            "Tool %r failed after %d attempts: %s",
            err.tool_name,
            err.attempts,
            err.last_error,
        )
        return None
```

`ToolValidationError` exposes:
*   `tool_name: str` — function name (`func.__name__`)
*   `attempts: int` — total attempts executed (`max(1, max_retries)`)
*   `last_error: str` — stringified final validation or retryable error

---

## 8. OpenTelemetry Telemetry Specification

`@validate_tool_output` enriches the active OpenTelemetry span while scrubbing sensitive PII/tokens (`scrub_sensitive_text`, `CWE-532`):

### Span Attributes
| Span Attribute | Type | Values | Description |
| :--- | :--- | :--- | :--- |
| `tool.validation.tool_name` | `str` | e.g. `"fetch_orders"` | Name of the decorated tool function. |
| `tool.validation.schema` | `str` | e.g. `"InventoryItem"` | Pydantic `schema_model.__name__` (when configured). |
| `tool.validation.status` | `str` | `"PASS"` \| `"FAIL"` | Final validation outcome. |
| `tool.validation.attempts` | `int` | `1 .. max_retries` | Attempt number that passed or exhausted retries. |
| `tool.validation.error` | `str` | Scrubbed message | Populated on `"FAIL"` after exhausting retries. |

### Span Events
Each retryable failure appends a span event named `tool.validation.retry` with attributes:
*   `tool.name` (`str`)
*   `attempt` (`int`)
*   `max_retries` (`int`)
*   `error` (`str`, scrubbed via `scrub_sensitive_text`)

---

## 9. Verification & Automated Test Suites

### Unit Tests
```bash
pytest -v tests/unit/test_resilience.py --eval-mode=in_memory
```

### Automated E2E Shell Suite (`tests/e2e_validate_tool_output/`)

| Script | Purpose |
| :--- | :--- |
| [`run_all_validation_tests.sh`](../tests/e2e_validate_tool_output/run_all_validation_tests.sh) | Master test runner executing all 4 validation test modules. |
| [`test_1_sync_and_async_validation.sh`](../tests/e2e_validate_tool_output/test_1_sync_and_async_validation.sh) | Validates sync & async execution across dicts, models, lists, tuples, and JSON strings. |
| [`test_2_retry_backoff_and_exhaustion.sh`](../tests/e2e_validate_tool_output/test_2_retry_backoff_and_exhaustion.sh) | Validates transient recovery, exponential backoff timing, and `ToolValidationError` properties. |
| [`test_3_assertion_callbacks_and_guards.sh`](../tests/e2e_validate_tool_output/test_3_assertion_callbacks_and_guards.sh) | Validates sync & async assertion callbacks and sync-on-async `TypeError` guard. |
| [`test_4_adk_agent_tool_validation.sh`](../tests/e2e_validate_tool_output/test_4_adk_agent_tool_validation.sh) | Validates Google ADK agent tool validation with OTel span assertions (`--offline` or live GCP). |

```bash
# Execute offline or against a GCP project
bash tests/e2e_validate_tool_output/run_all_validation_tests.sh --offline
bash tests/e2e_validate_tool_output/run_all_validation_tests.sh <your-gcp-project>
```
