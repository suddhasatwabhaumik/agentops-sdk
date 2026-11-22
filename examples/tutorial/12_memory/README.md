# 12 · Memory: Session Turns in Cloud SQL, Facts in Vertex AI Memory Bank (`v1.0.0`)

**What you learn:** how short-term session turn history (`CloudSQLSessionMemory`) and durable cross-session facts (`VertexMemoryBank`) compose cleanly via `CompositeMemory`.

```
                    ┌─ CloudSQLSessionMemory ──► Cloud SQL (PostgreSQL via Auth Proxy :5434)
CompositeMemory ────┤      add_message / get_messages       table agentops_tutorial_sessions
  .get_context()    └─ VertexMemoryBank ─────────► Vertex AI Agent Engine → Memory Bank
                           create_memory / retrieve_memories(query=...)
                           (canonical scope={"user_id": user_id} + search_query similarity recall)
```

## SDK calls used

| Call | What it does |
|---|---|
| `VertexMemoryBank(reasoning_engine_name=..., project_id=..., location=...)` | Persists long-term facts per user using canonical `scope={"user_id": user_id}` (`ADR-4`) |
| `create_memory(fact, user_id=..., memory_type=..., metadata=...)` | Stores a persistent fact with type and metadata preserved cleanly |
| `retrieve_memories(user_id=..., query=..., memory_type=..., limit=...)` | Lists recent memories or runs semantic `search_query` recall when `query` is provided; optional `memory_type` filtering uses bounded single-RPC over-fetch `min(max(limit * 4, 20), 100)` |
| `purge_memories(user_id=...)` | Deletes all long-term memories scoped to `user_id` |
| `CloudSQLSessionMemory(connection_url=..., table_name=...)` | Stores session turns in Cloud SQL PostgreSQL with cursor (`after_id`, `before_id`) and offset (`get_messages`) pagination |
| `CompositeMemory(session_memory, long_term_memory).get_context(..., strict=True)` | Merges short-term turns and relevant long-term facts; `strict=True` (default) surfaces backend failures instead of hiding errors |
| `InMemorySessionMemory`, `InMemoryLongTermMemory` | Explicit in-memory doubles for unit tests (never triggered implicitly) |

## GCP resources used

| Resource | Why | Access needed |
|---|---|---|
| Vertex AI Agent Engine (`00_setup/provision.py agent-engine`) | Hosts Memory Bank (`MEMORY_BANK_NAME`) | `roles/aiplatform.user` |
| Cloud SQL PostgreSQL instance + database (`00_setup/provision.py cloudsql`) | Stores session turns (`DATABASE_URL`) | `roles/cloudsql.client` to connect (`roles/cloudsql.admin` to create), `sqladmin.googleapis.com` enabled |
| Cloud SQL Auth Proxy on localhost `:5434` | Authenticated TLS tunnel to Cloud SQL | Binary from Cloud SQL Auth Proxy docs |

## Run

```bash
cloud-sql-proxy --port 5434 <project>:europe-west4:agentops-tutorial &
python examples/tutorial/12_memory/main.py
```

Expected: Part A stores two facts in Vertex Memory Bank and recalls the semantic match via `retrieve_memories(user_id=..., query=...)`; Part B inserts and paginates `2` turns in Cloud SQL; Part C prints the unified `CompositeMemory.get_context(...)` payload.
