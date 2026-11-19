#!/usr/bin/env python3
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

"""ADK-style Data Science Agent wrapped with v1 rate limiting and JWT RLS."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import secrets
import time
from typing import Any

from agentops_sdk import AsyncRateLimiter
from agentops_sdk import JWTAuthEngine
from agentops_sdk import RateLimitAlgorithm
from agentops_sdk import RateLimitExceededError
from agentops_sdk import RateLimitRule
from agentops_sdk import bind_execution_scope
from agentops_sdk import configure_telemetry
from agentops_sdk import rate_limit
from agentops_sdk import reset_telemetry
from agentops_sdk import trace_agent_span
from mcp_servers.src import auth_guard

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("adk-rate-limiting-agent")


class ADKDataScienceRateLimitedAgent:
    """Data Science Agent protected by JWT auth, RLS, and AsyncRateLimiter."""

    def __init__(
        self,
        project_id: str,
        tenant_id: str,
        rpm_limit: int = 10,
        burst: int = 5,
    ) -> None:
        self.project_id = project_id
        self.tenant_id = tenant_id
        self.rpm_limit = rpm_limit
        self.burst = burst
        self.jwt_engine = JWTAuthEngine(secret_key=secrets.token_urlsafe(48))
        self.jwt_token = self.jwt_engine.create_access_token(
            {
                "sub": f"agent-{tenant_id}",
                "tenant_id": tenant_id,
                "role": "data_scientist",
            }
        )
        self.limiter = AsyncRateLimiter()
        self.limiter.register_rule(
            f"tenant:{self.tenant_id}",
            RateLimitRule(
                rule_id=f"rule_{self.tenant_id}",
                rate=self.rpm_limit,
                period_seconds=60.0,
                burst=self.burst,
                max_concurrency=4,
                algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
            ),
        )
        self._exec = self._build_rate_limited_executor()

    def _build_rate_limited_executor(self):
        @rate_limit(
            rule_id=f"rule_{self.tenant_id}",
            rate=self.rpm_limit,
            period_seconds=60.0,
            burst=self.burst,
            max_concurrency=4,
            algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
            limiter=self.limiter,
        )
        @trace_agent_span(agent_name="adk_agent_query_bigquery")
        async def _run(sql_query: str) -> str:
            rewritten_sql, _claims = auth_guard.authorize_query(
                sql_query,
                token=self.jwt_token,
                dialect="bigquery",
                allowed_tables=["analytics_orders"],
                jwt_engine=self.jwt_engine,
            )
            await asyncio.sleep(0.01)
            return rewritten_sql

        return _run

    async def run_analytical_query(self, sql_query: str) -> dict[str, Any]:
        """Run one authorized, rate-limited SQL query."""
        claims = self.jwt_engine.decode_token(self.jwt_token)
        t0 = time.perf_counter()
        with bind_execution_scope(
            session_id=f"sess-{self.tenant_id}",
            user_id=claims["sub"],
            identity_token=self.jwt_token,
            claims=claims,
        ):
            try:
                rewritten = await self._exec(sql_query)
                elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
                return {
                    "status_code": 200,
                    "elapsed_ms": elapsed_ms,
                    "rewritten_sql": rewritten,
                }
            except RateLimitExceededError as exc:
                elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
                return {
                    "status_code": exc.status_code,
                    "elapsed_ms": elapsed_ms,
                    "retry_after_seconds": exc.retry_after_seconds,
                    "error": str(exc),
                }

    @trace_agent_span(agent_name="adk_data_science_batch_workflow")
    async def run_batch_investigation(
        self, query_count: int = 8
    ) -> dict[str, Any]:
        """Run concurrent analytical queries triggering burst throttling."""
        queries = [
            (
                "SELECT order_id, order_total FROM analytics_orders "
                "WHERE batch_idx = @batch_idx"
            )
            for _ in range(query_count)
        ]
        results = await asyncio.gather(
            *(self.run_analytical_query(q) for q in queries)
        )
        ok = [r for r in results if r["status_code"] == 200]
        throttled = [r for r in results if r["status_code"] == 429]
        return {
            "total_queries": query_count,
            "successful_count": len(ok),
            "throttled_count": len(throttled),
            "details": results,
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        default=os.getenv("GOOGLE_CLOUD_PROJECT", "local-offline"),
    )
    parser.add_argument("--tenant", default="tenant-analytics")
    parser.add_argument("--rpm", type=int, default=4)
    parser.add_argument("--burst", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    """Execute baseline query and throttled concurrent batch."""
    args = _parse_args()
    configure_telemetry(mode="off", service_name="adk-rate-limit-e2e")
    try:
        agent = ADKDataScienceRateLimitedAgent(
            project_id=args.project,
            tenant_id=args.tenant,
            rpm_limit=args.rpm,
            burst=args.burst,
        )
        baseline = asyncio.run(
            agent.run_analytical_query(
                "SELECT order_id, total FROM analytics_orders"
            )
        )
        assert baseline["status_code"] == 200, f"Baseline failed: {baseline}"
        assert agent.tenant_id in baseline["rewritten_sql"]

        batch = asyncio.run(agent.run_batch_investigation(query_count=7))
        assert batch["successful_count"] > 0
        assert batch["throttled_count"] > 0

        print(
            f"Benchmark OK | project={args.project} tenant={args.tenant} "
            f"ok={batch['successful_count']} 429={batch['throttled_count']}"
        )
    finally:
        reset_telemetry()


if __name__ == "__main__":
    main()
