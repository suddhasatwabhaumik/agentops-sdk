# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for agentops_sdk.sql AST guard, decorator, and RLS engine."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
import pytest

from agentops_sdk.domain import exceptions
from agentops_sdk.sql import RowLevelPolicyEngine
from agentops_sdk.sql import SQLASTValidator
from agentops_sdk.sql import apply_row_level_policy
from agentops_sdk.sql import validate_sql_query
from agentops_sdk.sql import validate_sql_query_decorator


def test_sql_ast_validator_allowlist_and_forbidden_constructs() -> None:
    res = validate_sql_query(
        "SELECT o.id FROM prod.orders AS o JOIN prod.items AS i ON o.id = i.id",
        allowed_tables=["prod.*"],
    )
    assert res.is_safe is True
    assert res.referenced_tables == ("prod.orders", "prod.items")

    set_op = validate_sql_query(
        "SELECT id FROM orders UNION ALL SELECT id FROM orders",
        allowed_tables=["orders"],
    )
    assert set_op.is_safe is True

    bad_cases = [
        ("", None),
        ("SELECT 1; DROP TABLE orders", None),
        ("DELETE FROM orders WHERE id = 1", None),
        ("SELECT /* DROP TABLE x */ id FROM orders", None),
        ("SELECT * FROM information_schema.tables", None),
        ("SELECT SESSION_USER()", None),
        ("SELECT pg_read_file('/etc/passwd')", "postgres"),
        ("SELECT id FROM secret_tbl", None),
    ]
    for sql, dialect in bad_cases:
        out = validate_sql_query(
            sql,
            dialect=dialect or "bigquery",
            allowed_tables=["orders"],
        )
        assert out.is_safe is False

    assert (
        SQLASTValidator(allowed_tables=[])
        .validate("SELECT 1 FROM orders")
        .is_safe
        is False
    )


async def test_validate_sql_query_decorator_sync_async_and_spans(
    otel_span_exporter: InMemorySpanExporter,
) -> None:
    @validate_sql_query_decorator(
        allowed_tables=["orders"], sql_params=("sql", "backup_sql")
    )
    def run_two(sql: str, **kwargs: str) -> tuple[str, str]:
        return sql, kwargs["backup_sql"]

    s1, s2 = run_two(
        "SELECT id /* strip */ FROM orders", backup_sql="SELECT 1 FROM orders"
    )
    assert "strip" not in s1 and "orders" in s2

    with pytest.raises(exceptions.SQLGovernanceError, match="Missing required"):
        run_two("SELECT id FROM orders")

    @validate_sql_query_decorator(
        allowed_tables=["orders"], auto_sanitize=False
    )
    async def run_async(query: str) -> str:
        return query

    with trace.get_tracer("test").start_as_current_span("sql_op"):
        raw = "SELECT id /* keep */ FROM orders"
        assert await run_async(raw) == raw
        with pytest.raises(exceptions.SQLGovernanceError):
            await run_async("DROP TABLE orders")

    spans = otel_span_exporter.get_finished_spans()
    assert spans[0].attributes["sql.ast.is_safe"] is False


def test_row_level_policy_scalar_list_joins_ctes_and_errors() -> None:
    engine = RowLevelPolicyEngine(
        policy_columns={"tenant_id": "tenant_id", "regions": "region_code"},
        target_tables=["orders"],
    )
    rewritten = engine.apply(
        "WITH cte AS (SELECT id FROM orders WHERE status = 'a' "
        "OR status = 'b') "
        "SELECT * FROM cte JOIN audit_log AS a ON cte.id = a.id",
        {"tenant_id": "t-1", "regions": ["NA", "EU"]},
    )
    assert "tenant_id = 't-1'" in rewritten
    assert "region_code IN ('NA', 'EU')" in rewritten
    assert rewritten.count("tenant_id = 't-1'") == 1

    int_sql = apply_row_level_policy(
        "SELECT id FROM orders", {"tenant_id": 101}
    )
    assert "tenant_id = 101" in int_sql

    admin_sql = apply_row_level_policy(
        "SELECT id FROM orders", {"role": "superadmin"}
    )
    assert admin_sql == "SELECT id FROM orders"

    for bad_claims in (
        {},
        {"tenant_id": True},
        {"tenant_id": ""},
        {"tenant_id": []},
        {"other": "x"},
    ):
        with pytest.raises(exceptions.SQLGovernanceError):
            apply_row_level_policy("SELECT id FROM orders", bad_claims)
    with pytest.raises(exceptions.SQLGovernanceError):
        apply_row_level_policy(
            "SELECT id FROM orders",
            {"tenant_id": "t1"},
            policy_columns={"tenant_id": "bad;col"},
        )
