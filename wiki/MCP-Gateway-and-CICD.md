# MCP Gateway & CI/CD (`v1.0.0`)

## FastMCP Governance Gateway (`mcp_servers/src/`)

- **Entrypoint**: `python -m mcp_servers.src.server` (`query_database_tool`, `query_databricks_tool`, `query_feature_store_tool`).
- **Fail-Closed Auth**: `HeaderBindingMiddleware` binds HTTP `Authorization: Bearer` headers (`MCP_REQUIRE_AUTH=true` default).
- **Entra & Tenant Mapping** (`auth_guard.py`): Maps `"tid"` -> `"tenant_id"` for RLS, separates `ENVIRONMENT=""` vs. `"development"` engine caches, catches `ConfigurationError` in `validate_and_authorize_sql()`, and rejects `bool`/non-scalar `tenant_id` in `authorize_feature_store_tenant()`.

## Feature Store Online Serving

- **`FeatureStoreAdminClient` & `FeatureStoreOnlineClient`**: Sync BigQuery offline tables to Bigtable-backed Vertex Feature Online Stores (`tools/feature_store_migration/`).

## CI/CD, Supply-Chain Hardening & Wheel Release Pipeline

- **`build-and-test`** (`.github/workflows/build-wheel.yml`, `contents: read`): Uses immutable 40-char commit-SHA GitHub Action pins; runs `ruff check` and `ruff format --check` (strict 80-char line length), `mypy --strict sdk/src/agentops_sdk mcp_servers/src tools`, `pytest` coverage + quality gate summary, `tools.release_notes.consolidate` (`_resolve_diff_base` tag-push safe), wheel build, `v{__version__}` tag check, `py.typed` check, `twine check`, and isolated off-`sys.path` wheel verification.
- **`release`** (`contents: write`): Publishes `.whl`, `RELEASE_NOTES.md`, and `SHA256SUMS` to GitHub Releases; downstream quality gates (`examples/cicd_templates/agent_quality_gates.yml`) enforce 40-SHA action pins and keyless Workload Identity Federation (`id-token: write`).
- **Container, Compose & CLI Hardening**: Enforces `@sha256:` container image digests (`mcp_servers/Dockerfile`, `cloudbuild.yaml`, `examples/full_stack/docker-compose.yaml`), `127.0.0.1` loopback-only Docker Compose bindings, and CLI secret redaction across `tools/`.

See [docs/mcp_integration.md](../docs/mcp_integration.md) and [docs/cicd_quality_gates_pytest_integration.md](../docs/cicd_quality_gates_pytest_integration.md).
