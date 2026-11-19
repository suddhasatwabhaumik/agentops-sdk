# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Instrumented agent tools, LLM call, evaluation, and audit helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from _validator_checks import RunLedger
from _workload_models import ENVIRONMENT
from _workload_models import FLOW_NAME
from _workload_models import KPI_ID
from _workload_models import PL_LINE
from _workload_models import PROJECT_NAME
from _workload_models import Dealer
from _workload_models import Scenario
from _workload_models import StockReport
from _workload_models import WorkloadContext
from opentelemetry import trace

from agentops_sdk import AsyncRateLimiter
from agentops_sdk import AuditLogEntry
from agentops_sdk import RateLimitExceededError
from agentops_sdk import apply_row_level_policy
from agentops_sdk import evaluate_agent_response
from agentops_sdk import log_llm_metrics
from agentops_sdk import rate_limit
from agentops_sdk import trace_agent_span
from agentops_sdk import trace_llm_call
from agentops_sdk import validate_sql_query_decorator
from agentops_sdk import validate_tool_output

logger = logging.getLogger("agentops.full_stack")

TOOL_LIMITER = AsyncRateLimiter()
BULK_LIMITER = AsyncRateLimiter()


@validate_sql_query_decorator(
    allowed_tables=["parts_inventory"], sql_params=["sql"]
)
def query_parts_inventory(sql: str) -> list[dict[str, Any]]:
    """Simulate a validated SQL warehouse query."""
    del sql
    return [{"part_number": "FLT-2750", "units_available": 42}]


@rate_limit(
    rule_id="dealer-stock-tool",
    rate=600,
    period_seconds=60.0,
    max_concurrency=8,
    limiter=TOOL_LIMITER,
)
@validate_tool_output(schema_model=StockReport, max_retries=3)
async def fetch_dealer_stock(
    *,
    dealer_id: str,
    rows: list[dict[str, Any]],
    behaviour: str,
    attempts: list[int],
) -> dict[str, Any]:
    """Simulate a flaky dealer stock API triggering tool-output retries."""
    attempts.append(1)
    await asyncio.sleep(0.02)
    invalid = behaviour == "invalid" or (
        behaviour == "flaky" and len(attempts) == 1
    )
    units = -1 if invalid else rows[0]["units_available"]
    return {
        "dealer_id": dealer_id,
        "part_number": rows[0]["part_number"],
        "units_available": units,
    }


@rate_limit(
    rule_id="price-sync-bulk",
    rate=5,
    period_seconds=10.0,
    burst=5,
    max_concurrency=3,
    limiter=BULK_LIMITER,
)
async def sync_price_list() -> int:
    """Protected endpoint used to exercise burst rate-limit rejections."""
    await asyncio.sleep(0.05)
    return 1


@trace_agent_span(agent_name="inventory_agent")
async def inventory_agent(
    dealer: Dealer, sql: str, tool_output: str, ledger: RunLedger
) -> dict[str, Any]:
    """Run RLS rewrite, SQL AST guard, and schema-validated stock lookup."""
    secured_sql = apply_row_level_policy(
        sql,
        {"region": dealer.region},
        policy_columns={"region": "region"},
    )
    trace.get_current_span().set_attribute("db.query.text", secured_sql)
    rows = query_parts_inventory(sql=secured_sql)
    ledger.rate_limit_checks += 1
    return await fetch_dealer_stock(
        dealer_id=dealer.dealer_id,
        rows=rows,
        behaviour=tool_output,
        attempts=[],
    )


@trace_llm_call(
    kpi_id=KPI_ID,
    pl_impact_line=PL_LINE,
    project_name=PROJECT_NAME,
    environment=ENVIRONMENT,
)
async def generate_answer(
    prompt: str, *, model: str, fails: bool, leaks_pii: bool
) -> dict[str, Any]:
    """Simulate Gemini generation returning token usage metadata."""
    await asyncio.sleep(0.08 if model.endswith("pro") else 0.03)
    if fails:
        raise TimeoutError("504 Deadline Exceeded (simulated)")
    text = "FLT-2750 fuel filters: 42 units available to ship this week."
    if leaks_pii:
        text += " Contact parts.desk@example.com or +1 555 010 2233."
    return {
        "model_name": model,
        "text": text,
        "finish_reason": "STOP",
        "usage": {
            "prompt_token_count": max(1, len(prompt) // 4),
            "candidates_token_count": max(1, len(text) // 4),
            "cached_content_token_count": 128 if model.endswith("flash") else 0,
        },
    }


@trace_agent_span(agent_name="answer_agent")
async def answer_agent(
    prompt: str, model: str, scenario: Scenario, ledger: RunLedger
) -> dict[str, Any]:
    """Invoke LLM stub and record span token attributes."""
    ledger.llm_calls += 1
    response = await generate_answer(
        prompt,
        model=model,
        fails=scenario.llm_fails,
        leaks_pii=scenario.leaks_pii,
    )
    usage = response["usage"]
    log_llm_metrics(
        model_name=response["model_name"],
        input_tokens=usage["prompt_token_count"],
        output_tokens=usage["candidates_token_count"],
        cached_tokens=usage["cached_content_token_count"],
    )
    ledger.input_tokens += usage["prompt_token_count"]
    ledger.output_tokens += usage["candidates_token_count"]
    return response


def record_quality_eval(
    question: str, stock: dict[str, Any], safe_text: str, ctx: WorkloadContext
) -> None:
    """Run InMemoryEvaluator and emit structured eval telemetry."""
    payload = [
        {
            "prompt": question,
            "context": json.dumps(stock),
            "response": safe_text,
        }
    ]
    res = evaluate_agent_response(
        payload, evaluator=ctx.evaluator, record_report=False
    )
    grounded = res.scores.get("groundedness", 0.0)
    relevance = res.scores.get("question_answering_relevance", 0.0)
    span = trace.get_current_span()
    span.set_attribute("eval.groundedness_score", grounded)
    span.set_attribute("eval.relevance_score", relevance)
    span.set_attribute("eval.is_passing", res.is_passing)
    eval_fields = {
        "groundedness": grounded,
        "relevance": relevance,
        "is_passing": res.is_passing,
        "evaluator": "in_memory",
    }
    logger.info(
        "Quality eval | grounded=%.2f rel=%.2f pass=%s",
        grounded,
        relevance,
        res.is_passing,
        extra={
            "json_fields": {
                "event_type": "agent_quality_evaluation",
                "eval": eval_fields,
            }
        },
    )


async def write_audit(
    ctx: WorkloadContext,
    trace_id: str,
    dealer: Dealer,
    question: str,
    status: str,
    started: float,
    sql: str | None = None,
    error: str | None = None,
) -> None:
    """Append an immutable compliance entry to the configured AuditLedger."""
    await ctx.audit.log_entry(
        AuditLogEntry(
            trace_id=trace_id,
            session_id=ctx.ledger.run_id,
            user_id=dealer.username,
            agent_name=FLOW_NAME,
            input_query=question,
            generated_sql=sql,
            execution_status=status,
            error_message=error,
            latency_ms=(time.perf_counter() - started) * 1000,
            safety_sanitization_status="LOCAL_RULES",
        )
    )
    ctx.ledger.audit_entries += 1


@trace_agent_span(agent_name="price_sync_agent")
async def price_sync_agent(burst: int, ledger: RunLedger) -> int:
    """Fire a burst that triggers RateLimitExceededError rejections."""
    ledger.trace_ids.append(
        format(trace.get_current_span().get_span_context().trace_id, "032x")
    )
    results = await asyncio.gather(
        *(sync_price_list() for _ in range(burst)),
        return_exceptions=True,
    )
    rejected = sum(isinstance(r, RateLimitExceededError) for r in results)
    ledger.rate_limit_checks += burst
    ledger.rate_limit_rejections += rejected
    trace.get_current_span().set_attribute(
        "agentops.rate_limit.rejected", rejected
    )
    return rejected
