# MCP Gateway & CI/CD (`v1.0.0`)

## FastMCP Governance Gateway (`mcp_servers/src/`)

- **Entrypoint**: `python -m mcp_servers.src.server` (`query_database_tool`, `query_databricks_tool`, `query_feature_store_tool`).
- **Fail-Closed Auth**: `HeaderBindingMiddleware` binds HTTP `Authorization: Bearer` headers (`MCP_REQUIRE_AUTH=true` default).
- **Entra & Tenant Mapping** (`auth_guard.py`): Maps `"tid"` -> `"tenant_id"` for RLS, separates `ENVIRONMENT=""` vs. `"development"` engine caches, catches `ConfigurationError` in `validate_and_authorize_sql()`, and rejects `bool`/non-scalar `tenant_id` in `authorize_feature_store_tenant()`.

## Feature Store Online Serving

- **`FeatureStoreAdminClient` & `FeatureStoreOnlineClient`**: Sync BigQuery offline tables to Bigtable-backed Vertex Feature Online Stores (`tools/feature_store_migration/`).

## CI/CD & Wheel Release Pipeline (`.github/workflows/build-wheel.yml`)

- **`build-and-test`** (`contents: read`): `ruff check`, `ruff format --check`, `mypy --strict`, `pytest` coverage + quality gate summary, `tools.release_notes.consolidate` (`_resolve_diff_base` tag-push safe), wheel build, `v{__version__}` tag check, `py.typed` check, `twine check`, and isolated off-`sys.path` wheel verification.
- **`release`** (`contents: write`): Publishes `.whl`, `RELEASE_NOTES.md`, and `SHA256SUMS` to GitHub Releases.

See [docs/mcp_integration.md](../docs/mcp_integration.md) and [docs/cicd_quality_gates_pytest_integration.md](../docs/cicd_quality_gates_pytest_integration.md).
