# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""Flow orchestrator and iteration driver for the full-stack demo."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from _validator_checks import RunLedger
from _workload_agents import answer_agent
from _workload_agents import inventory_agent
from _workload_agents import price_sync_agent
from _workload_agents import record_quality_eval
from _workload_agents import write_audit
from _workload_models import DEALERS
from _workload_models import ENVIRONMENT
from _workload_models import FLOW_NAME
from _workload_models import KPI_ID
from _workload_models import MODELS
from _workload_models import PL_LINE
from _workload_models import PROJECT_NAME
from _workload_models import SCENARIOS
from _workload_models import SERVICE_NAME
from _workload_models import Dealer
from _workload_models import Scenario
from _workload_models import WorkloadContext
from _workload_models import build_context
from opentelemetry import trace

from agentops_sdk import ToolValidationError
from agentops_sdk import UseCaseTracker
from agentops_sdk import bind_execution_scope
from agentops_sdk import create_access_token
from agentops_sdk import decode_jwt_token
from agentops_sdk import trace_agent_flow
from agentops_sdk.domain.exceptions import AgentOpsError
from agentops_sdk.domain.exceptions import SQLGovernanceError

__all__ = [
    "ENVIRONMENT",
    "FLOW_NAME",
    "SERVICE_NAME",
    "RunLedger",
    "build_context",
    "dealer_parts_advisor",
    "run_iteration",
    "run_workload",
]


async def _run_pipeline_steps(
    question: str,
    dealer: Dealer,
    model: str,
    scenario: Scenario,
    ctx: WorkloadContext,
) -> tuple[dict[str, Any], dict[str, Any]]:
    stock = await inventory_agent(
        dealer, scenario.sql, scenario.tool_output, ctx.ledger
    )
    prompt = ctx.prompts.render_prompt(
        FLOW_NAME,
        {
            "dealer_id": dealer.dealer_id,
            "region": dealer.region,
            "question": question,
            "stock": json.dumps(stock),
        },
    )
    answer = await answer_agent(prompt, model, scenario, ctx.ledger)
    return stock, answer


async def _execute_guarded_pipeline(
    ctx: WorkloadContext,
    trace_id: str,
    dealer: Dealer,
    question: str,
    model: str,
    scenario: Scenario,
    started: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        return await _run_pipeline_steps(question, dealer, model, scenario, ctx)
    except (AgentOpsError, TimeoutError) as err:
        await write_audit(
            ctx,
            trace_id,
            dealer,
            question,
            "FAILED",
            started,
            sql=scenario.sql,
            error=str(err),
        )
        raise


@trace_agent_flow(
    flow_name=FLOW_NAME,
    agent_role="orchestrator",
    kpi_id=KPI_ID,
    pl_impact_line=PL_LINE,
    project_name=PROJECT_NAME,
    environment=ENVIRONMENT,
)
async def dealer_parts_advisor(
    question: str,
    *,
    dealer: Dealer,
    model: str,
    scenario: Scenario,
    session_id: str,
    ctx: WorkloadContext,
) -> dict[str, Any]:
    """Execute safety, memory, inventory/LLM, DLP, eval, and audit flow."""
    started = time.perf_counter()
    trace_id = format(
        trace.get_current_span().get_span_context().trace_id, "032x"
    )
    ctx.ledger.trace_ids.append(trace_id)

    safety = ctx.guard.check_input(question)
    if not safety.allowed:
        err = safety.violation_category
        await write_audit(
            ctx, trace_id, dealer, question, "BLOCKED", started, error=err
        )
        return {"status": "BLOCKED", "reason": err}

    ctx.memory.add_message(session_id, "user", question)
    stock, answer = await _execute_guarded_pipeline(
        ctx, trace_id, dealer, question, model, scenario, started
    )
    safe_text = await ctx.sanitizer.scrub_text(answer["text"])
    record_quality_eval(question, stock, safe_text, ctx)
    ctx.memory.add_message(session_id, "assistant", safe_text)
    await write_audit(
        ctx, trace_id, dealer, question, "SUCCESS", started, sql=scenario.sql
    )
    return {"status": "ANSWERED", "answer": safe_text}


async def _dispatch_flow(
    scenario: Scenario,
    dealer: Dealer,
    model: str,
    session_id: str,
    ctx: WorkloadContext,
) -> str:
    try:
        res = await dealer_parts_advisor(
            scenario.question,
            dealer=dealer,
            model=model,
            scenario=scenario,
            session_id=session_id,
            ctx=ctx,
        )
        return (
            "answered" if res["status"] == "ANSWERED" else "blocked_by_safety"
        )
    except ToolValidationError:
        return "tool_output_rejected"
    except SQLGovernanceError:
        return "blocked_by_sql_guard"
    except TimeoutError:
        return "llm_error"


async def _run_scenario(
    iteration: int, index: int, scenario: Scenario, ctx: WorkloadContext
) -> None:
    dealer = DEALERS[(iteration + index) % len(DEALERS)]
    model = MODELS[(iteration + index) % len(MODELS)]
    token = create_access_token(
        {
            "sub": dealer.username,
            "tenant_id": dealer.dealer_id,
            "region": dealer.region,
        },
        secret_key=ctx.jwt_key,
    )
    claims = decode_jwt_token(token, secret_key=ctx.jwt_key)
    session_id = f"{ctx.ledger.run_id}-s{iteration}-{index}"

    with (
        bind_execution_scope(
            session_id=session_id,
            user_id=claims["sub"],
            identity_token=token,
            claims=claims,
        ),
        UseCaseTracker(
            FLOW_NAME,
            exporter=ctx.case_exporter,
            business_kpi=KPI_ID,
            pl_impact_line=PL_LINE,
        ) as tracker,
    ):
        outcome = await _dispatch_flow(scenario, dealer, model, session_id, ctx)
        tracker.add_transactions(1)
        ctx.ledger.case_records += 1
        if outcome != scenario.outcome:
            raise RuntimeError(
                f"Scenario {scenario.outcome!r} ended as {outcome!r}"
            )
        ctx.ledger.flows[outcome] = ctx.ledger.flows.get(outcome, 0) + 1


async def run_iteration(iteration: int, ctx: WorkloadContext) -> None:
    """Execute one full round of scenarios, burst throttling, and fallback."""
    for idx, scenario in enumerate(SCENARIOS):
        await _run_scenario(iteration, idx, scenario, ctx)

    dealer = DEALERS[iteration % len(DEALERS)]
    with bind_execution_scope(
        session_id=f"{ctx.ledger.run_id}-sync-{iteration}",
        user_id=dealer.username,
        claims={"tenant_id": dealer.dealer_id},
    ):
        await price_sync_agent(burst=10, ledger=ctx.ledger)

    ctx.prompts_during_outage.render_prompt(
        FLOW_NAME,
        {
            "dealer_id": "n/a",
            "region": "n/a",
            "question": "fallback",
            "stock": "{}",
        },
    )


async def run_workload(
    ctx: WorkloadContext, iterations: int, loop_seconds: float
) -> None:
    """Run requested iterations or loop until deadline."""
    deadline = time.monotonic() + loop_seconds
    iteration = 0
    while iteration < iterations or time.monotonic() < deadline:
        await run_iteration(iteration, ctx)
        iteration += 1
        total = sum(ctx.ledger.flows.values())
        print(f"  iteration {iteration}: {total} flows so far", flush=True)
        if time.monotonic() < deadline:
            await asyncio.sleep(2)
    ctx.ledger.iterations = iteration
