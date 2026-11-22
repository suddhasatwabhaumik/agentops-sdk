# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for agentops_sdk.sql AST guard, decorator, and RLS engine."""

from __future__ import annotations

import sqlite3

from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
import pytest

from agentops_sdk.domain import exceptions
from agentops_sdk.sql import RowLevelPolicyEngine
from agentops_sdk.sql import SQLASTValidator
from agentops_sdk.sql import apply_row_level_policy
from agentops_sdk.sql import rls
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


def test_rls_cte_case_nested_exists_parenthesized_joins_and_qualifiers() -> (
    None
):
    claims = {"tenant_id": "tenant-acme"}
    pg_cte = apply_row_level_policy(
        'WITH "SECRET" AS (SELECT 1 AS id) SELECT * FROM SECRET',
        claims,
        dialect="postgres",
    )
    assert "SECRET.tenant_id = 'tenant-acme'" in pg_cte

    nested_exists = apply_row_level_policy(
        "SELECT o.id FROM orders AS o WHERE EXISTS "
        "(WITH secret AS (SELECT id FROM items) SELECT 1 FROM secret)",
        claims,
    )
    assert nested_exists.count("tenant_id = 'tenant-acme'") == 2

    paren_join = apply_row_level_policy(
        "SELECT p.id FROM (pub p JOIN secret s ON p.id = s.id)",
        claims,
        target_tables=["secret"],
    )
    assert "s.tenant_id = 'tenant-acme'" in paren_join
    assert "p.tenant_id" not in paren_join

    left_on = apply_row_level_policy(
        "SELECT p.id, s.id FROM pub p LEFT JOIN secret s ON p.id = s.id",
        claims,
        target_tables=["secret"],
    )
    assert "ON (p.id = s.id) AND (s.tenant_id = 'tenant-acme')" in left_on
    assert "WHERE" not in left_on

    left_using = apply_row_level_policy(
        "SELECT id FROM pub LEFT JOIN secret USING (id)",
        claims,
        target_tables=["secret"],
    )
    assert "WHERE (secret.tenant_id = 'tenant-acme')" in left_using

    left_natural = apply_row_level_policy(
        "SELECT id FROM pub NATURAL LEFT JOIN secret",
        claims,
        target_tables=["secret"],
    )
    assert "WHERE (secret.tenant_id = 'tenant-acme')" in left_natural

    quoted = apply_row_level_policy(
        'SELECT id FROM "Orders"',
        claims,
        dialect="postgres",
    )
    assert "\"Orders\".tenant_id = 'tenant-acme'" in quoted

    dup_short = apply_row_level_policy(
        "SELECT a.id FROM db1.orders JOIN db2.orders ON db1.orders.id = a.id",
        claims,
    )
    assert "db1.orders.tenant_id = 'tenant-acme'" in dup_short
    assert "db2.orders.tenant_id = 'tenant-acme'" in dup_short


def test_rls_ml_predict_tvfs_wildcards_empty_targets_and_uncovered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = {"tenant_id": "tenant-acme"}
    ml_sql = (
        "SELECT * FROM ML.PREDICT("
        "MODEL `proj.ds.mdl`, (SELECT id FROM proj.ds.orders)"
        ") AS pred"
    )
    assert validate_sql_query(ml_sql, allowed_tables=["proj.ds.*"]).is_safe
    ml_rls = apply_row_level_policy(ml_sql, claims, target_tables=["proj.ds.*"])
    assert ml_rls.count("tenant_id = 'tenant-acme'") == 2
    assert "pred.tenant_id = 'tenant-acme'" in ml_rls

    for bad_sql, dialect in (
        (
            "SELECT * FROM orders JOIN my_tvf(1) AS t ON orders.id = t.id",
            "bigquery",
        ),
        (
            "SELECT * FROM orders, my_tvf(1) AS t",
            "bigquery",
        ),
        (
            "SELECT * FROM orders JOIN ML.PREDICT("
            "MODEL `proj.ds.mdl`, (SELECT id FROM proj.ds.orders)"
            ") AS p ON orders.id = p.id",
            "bigquery",
        ),
        ("SELECT * FROM TableFromRows([(1, 'a')]) AS t", "bigquery"),
        (
            "SELECT * FROM orders PIVOT(SUM(amt) FOR q IN ('Q1', 'Q2'))",
            "bigquery",
        ),
        ("SELECT * FROM orders UNPIVOT(val FOR col IN (a, b))", "bigquery"),
        ("SELECT * FROM orders AS o(id, tenant_id)", "postgres"),
    ):
        with pytest.raises(exceptions.SQLGovernanceError):
            apply_row_level_policy(bad_sql, claims, dialect=dialect)

    with pytest.raises(
        exceptions.SQLGovernanceError, match="must not be empty"
    ):
        apply_row_level_policy(
            "SELECT id FROM orders", claims, target_tables=[]
        )

    monkeypatch.setattr(rls, "_apply_select_scope", lambda *_a, **_kw: None)
    with pytest.raises(
        exceptions.SQLGovernanceError, match="failed to cover table"
    ):
        apply_row_level_policy(
            "SELECT id FROM orders", claims, target_tables=["orders"]
        )


def test_rls_live_sqlite_two_tenant_leak_verification() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE orders (id INT, tenant_id TEXT, val TEXT)")
    conn.execute("CREATE TABLE secret (id INT, tenant_id TEXT, note TEXT)")
    conn.execute("CREATE TABLE pub (id INT, label TEXT)")
    conn.executemany(
        "INSERT INTO orders VALUES (?, ?, ?)",
        [
            (1, "tenant-acme", "acme-1"),
            (2, "tenant-beta", "beta-leak"),
        ],
    )
    conn.executemany(
        "INSERT INTO secret VALUES (?, ?, ?)",
        [
            (1, "tenant-acme", "acme-sec"),
            (2, "tenant-beta", "beta-sec-leak"),
        ],
    )
    conn.executemany("INSERT INTO pub VALUES (?, ?)", [(1, "p1"), (2, "p2")])

    queries = [
        ("SELECT val FROM orders", None),
        ("SELECT val FROM orders WHERE id = 1 OR id = 2", None),
        (
            "WITH cte AS (SELECT id, val FROM orders) SELECT val FROM cte",
            None,
        ),
        (
            "SELECT p.id, s.note FROM pub p LEFT JOIN secret s ON p.id = s.id",
            ["secret"],
        ),
        (
            "SELECT s.note FROM (pub p JOIN secret s ON p.id = s.id)",
            ["secret"],
        ),
        (
            "SELECT o.val FROM orders o WHERE EXISTS "
            "(SELECT 1 FROM secret s WHERE s.id = o.id)",
            None,
        ),
        ("SELECT val FROM orders UNION ALL SELECT note FROM secret", None),
    ]
    claims = {"tenant_id": "tenant-acme"}
    for raw_sql, targets in queries:
        rewritten = apply_row_level_policy(
            raw_sql, claims, dialect="sqlite", target_tables=targets
        )
        rows = conn.execute(rewritten).fetchall()
        flat = " ".join(str(cell) for row in rows for cell in row if cell)
        assert "beta-leak" not in flat
        assert "beta-sec-leak" not in flat
    conn.close()
