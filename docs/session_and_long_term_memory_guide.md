# Conversational Session & Long-Term Memory Architecture Guide (`v1.0.0`)

```
Copyright 2026 Google. This software is provided as-is, without
warranty or representation for any use or purpose. Your use of it is
subject to your agreement with Google.
```

## 1. Executive Summary

Enterprise AI agents require two distinct memory tiers organized behind typed port contracts in `agentops_sdk.ports.memory` and implemented in `agentops_sdk.memory`:

1. **Short-Term Session Memory (`SessionMemoryPort`)**:
   - Persists exact conversation turns (`user`, `assistant`, `system`, `tool`) keyed by `session_id`.
   - Supports **dual pagination** (`limit` + `offset`, plus cursor keys `after_id` and `before_id`) capped cleanly at `MAX_PAGE_SIZE = 200` (`DEFAULT_PAGE_SIZE = 50`).
   - Production adapter `CloudSQLSessionMemory` uses SQLAlchemy Core against PostgreSQL / Google Cloud SQL (`[cloudsql]` extra) with connection pre-ping (`pool_pre_ping=True`) and 30-minute recycling (`pool_recycle=1800`).
   - Explicit offline test double: `InMemorySessionMemory` (no silent fallback to SQLite or in-memory storage).

2. **Cross-Session Long-Term Memory (`LongTermMemoryPort`)**:
   - Persists durable facts and preferences scoped cleanly per user (`scope={"user_id": user_id}`).
   - `VertexMemoryBank` wraps Vertex AI Agent Engine Memory Bank using canonical `scope={"user_id": user_id}` for both writes and reads (`ADR-4`), persisting `memory_type` and custom metadata inside `AgentEngineMemoryConfig` metadata and running native `search_query` semantic similarity recall.
   - Explicit offline test double: `InMemoryLongTermMemory` (no implicit env-flag mock switches).

3. **Unified Composite Coordinator (`CompositeMemory`)**:
   - Coordinates `SessionMemoryPort` and `LongTermMemoryPort` via `get_context()` / `aget_context()`, returning unified prompt context (`turns`, `memories`, `short_term_history`, `long_term_memories`).
   - Defaults to **fail-closed `strict=True`** so storage errors surface loudly rather than returning silently truncated context unless a caller explicitly requests `strict=False`.

---

## 2. Architecture & Data Flow

```mermaid
flowchart TD
    UI["Frontend Chat UI / Agent"] -->|1. Request| Gateway["FastAPI / Agent Runtime"]
    
    subgraph Composite_Memory_Facade["agentops_sdk.memory"]
        Gateway -->|2. get_context / aget_context| CM["CompositeMemory (strict=True default)"]
        CM -->|Short-Term History| SMP["SessionMemoryPort (Protocol)"]
        CM -->|Long-Term Facts| LTMP["LongTermMemoryPort (Protocol)"]
        
        SMP -->|Relational| CSM["CloudSQLSessionMemory ([cloudsql])"]
        SMP -->|Offline Tests| ISM["InMemorySessionMemory"]
        LTMP -->|Vector Semantic Recall| VMB["VertexMemoryBank"]
        LTMP -->|Offline Tests| ILTM["InMemoryLongTermMemory"]
    end
    
    subgraph Storage_Layer["Persistence & Infrastructure"]
        CSM --> Pool["SQLAlchemy Pool (Pre-Ping & 30m Recycle)"]
        Pool --> Indexes["Composite Index (session_id, id)"]
        Indexes --> DB[("Google Cloud SQL (PostgreSQL)")]
        VMB --> VertexAPI["Vertex AI Agent Engine Memory Bank (scope={'user_id': uid})"]
    end
```

---

## 3. Domain Data Models (`agentops_sdk.domain.models`)

### 3.1 `ConversationTurn`
```python
from typing import Any

from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    """Represents a single conversational turn in session memory."""

    id: int | None = None
    session_id: str
    role: str
    content: str
    timestamp: str = Field(default_factory=...)
    metadata: dict[str, Any] = Field(default_factory=dict)
```

### 3.2 `MemoryItem`
```python
class MemoryItem(BaseModel):
    """Represents a long-term memory item stored in or retrieved from a memory bank."""

    memory_id: str = ""
    user_id: str
    fact: str
    memory_type: str = "fact"
    score: float | None = None
    created_at: str = Field(default_factory=...)
    metadata: dict[str, Any] = Field(default_factory=dict)
```

---

## 4. Short-Term Conversational Session Memory

### 4.1 `SessionMemoryPort` Contract (`ports/memory.py`)
| Method | Type | Description |
| :--- | :--- | :--- |
| `add_message(session_id, role, content, metadata=None)` | Sync | Appends a turn and returns `ConversationTurn`. |
| `aadd_message(session_id, role, content, metadata=None)` | Async | Non-blocking turn append (`asyncio.to_thread`). |
| `get_messages(session_id, *, limit=50, offset=0, after_id=None, before_id=None)` | Sync | Returns ascending-ID `list[ConversationTurn]` slice bounded by `MAX_PAGE_SIZE = 200`. |
| `aget_messages(...)` | Async | Non-blocking structured turn retrieval. |
| `clear_session(session_id)` / `aclear_session(session_id)` | Sync/Async | Deletes all turns belonging to `session_id` and returns deleted count. |

### 4.2 `CloudSQLSessionMemory` Relational Backend (`memory/cloud_sql.py`)
`CloudSQLSessionMemory(connection_url, *, creator=None, table_name="agentops_session_messages", auto_create_schema=True)` manages relational tables via SQLAlchemy Core:

```sql
CREATE TABLE agentops_session_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, -- database-specific sequence
    session_id VARCHAR(256) NOT NULL,
    role VARCHAR(64) NOT NULL,
    content TEXT NOT NULL,
    timestamp VARCHAR(64) NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE INDEX ix_agentops_session_messages_session_id_id
    ON agentops_session_messages (session_id, id);
```

- Missing `connection_url` raises `ConfigurationError` (no hidden SQLite fallback). Missing `[cloudsql]` extra also raises `ConfigurationError` prompting installation of `agentops-sdk[cloudsql]`.
- Non-SQLite engines enable `pool_pre_ping=True` and `pool_recycle=1800`.

### 4.3 Dual Pagination Examples
```python
# Cursor pagination: turns older than ID 150 (returned in ascending ID order)
older_turns = await memory.aget_messages(
    session_id="sess-1", before_id=150, limit=20
)

# Cursor pagination: turns newer than ID 150
newer_turns = await memory.aget_messages(
    session_id="sess-1", after_id=150, limit=20
)

# Offset pagination: page 1
page_1 = await memory.aget_messages(session_id="sess-1", limit=10, offset=0)
```

---

## 5. Cross-Session Long-Term Memory (`VertexMemoryBank`)

### 5.1 `LongTermMemoryPort` Contract (`ports/memory.py`)
| Method | Type | Return Type | Description |
| :--- | :--- | :--- | :--- |
| `create_memory(fact, *, user_id, memory_type="fact", metadata=None, ttl_seconds=None)` | Sync | `MemoryItem` | Persists a durable fact under `user_id`. |
| `acreate_memory(...)` | Async | `MemoryItem` | Non-blocking memory creation. |
| `retrieve_memories(*, user_id, query=None, limit=5)` | Sync | `list[MemoryItem]` | Simple list recall or semantic vector similarity search (`query`). |
| `aretrieve_memories(...)` | Async | `list[MemoryItem]` | Non-blocking recall. |
| `purge_memories(*, user_id)` / `apurge_memories(*, user_id)` | Sync/Async | `int` | Deletes all memories for `user_id` and returns purge count. |

### 5.2 `VertexMemoryBank` Implementation (`memory/vertex_bank.py`, ADR-4)
`VertexMemoryBank(reasoning_engine_name, *, project_id=None, location=None, client=None)` connects directly via `vertexai.Client(project=proj, location=loc)`:

1. **Flexible Resource Naming**: Accepts either a bare engine ID (`"1234567890"`) or a full resource name (`"projects/{project}/locations/{location}/reasoningEngines/{id}"`).
2. **Canonical Scope (`scope={"user_id": uid}`)**: Writes and reads both use `scope={"user_id": user_id}` consistently so memories saved with `memory_type` or custom metadata are always discoverable.
3. **Metadata & TTL Config**: Encodes `memory_type` and custom key-value `metadata` into typed `metadata` entries and optional `ttl=f"{ttl_seconds}s"` inside `AgentEngineMemoryConfig`.
4. **Native Semantic Similarity Search**: When `query` is provided, sends `similarity_search_params={"search_query": query, "top_k": limit}` (vs `simple_retrieval_params={"page_size": limit}` when omitted).
5. **User ID Validation & Fail-Loud Errors**: Enforces `validate_user_id()` (`ValueError` on null/blank/`/`/`..`/control chars) and wraps API failures cleanly in `MemoryBankError`.
6. **Explicit Offline Testing**: Use `InMemoryLongTermMemory()` directly in unit tests instead of environment switches.

---

## 6. Unified Context Coordination (`CompositeMemory`)

[`CompositeMemory`](../sdk/src/agentops_sdk/memory/composite.py) orchestrates both ports cleanly:

```python
from agentops_sdk.memory import (
    CloudSQLSessionMemory,
    CompositeMemory,
    VertexMemoryBank,
)

session_mem = CloudSQLSessionMemory(connection_url="postgresql+psycopg2://user:pass@127.0.0.1:5434/agentops")
long_term_mem = VertexMemoryBank(
    reasoning_engine_name="projects/my-gcp-project/locations/europe-west4/reasoningEngines/123456789",
    project_id="my-gcp-project",
    location="europe-west4",
)

composite = CompositeMemory(session_memory=session_mem, long_term_memory=long_term_mem)

context = await composite.aget_context(
    session_id="session-user-987",
    user_id="user-987",
    query="engine hydraulic filters",
    turn_limit=20,
    memory_limit=5,
    strict=True,  # Default: raises on backend failure; pass strict=False to degrade cleanly
)
```

Returned payload keys:
- `session_id`: Active session identifier
- `user_id`: Target user identifier
- `turns`: `list[ConversationTurn]`
- `memories`: `list[MemoryItem]`
- `short_term_history`: `[{"role": ..., "content": ..., "metadata": ...}, ...]`
- `long_term_memories`: Serialized dictionaries of retrieved `MemoryItem` instances

You can also call `consolidate_session(session_id, user_id, ...)` / `aconsolidate_session(...)` (using Vertex Agent Engine `generate_memories` or custom extractor) to convert session dialogue into durable long-term facts.

---

## 7. FastAPI Integration Pattern

```python
from fastapi import FastAPI, Query

from agentops_sdk.domain.models import ConversationTurn, MemoryItem
from agentops_sdk.memory import (
    CloudSQLSessionMemory,
    CompositeMemory,
    VertexMemoryBank,
)

app = FastAPI(title="AgentOps Unified Memory Gateway")

session_memory = CloudSQLSessionMemory(
    connection_url="postgresql+psycopg2://user:pass@127.0.0.1:5434/agentops"
)
long_term_memory = VertexMemoryBank(
    reasoning_engine_name="projects/my-project/locations/europe-west4/reasoningEngines/987654321",
    project_id="my-project",
    location="europe-west4",
)
composite = CompositeMemory(
    session_memory=session_memory, long_term_memory=long_term_memory
)


@app.post("/v1/chat/{session_id}/turn")
async def record_chat_turn(
    session_id: str, role: str, content: str, user_id: str | None = None
):
    await composite.aadd_message(
        session_id=session_id, role=role, content=content, metadata={"user_id": user_id}
    )
    return {"status": "recorded"}


@app.get("/v1/chat/{session_id}/context")
async def get_agent_context(session_id: str, user_id: str, query: str | None = None):
    return await composite.aget_context(
        session_id=session_id, user_id=user_id, query=query, strict=False
    )


@app.get("/v1/chat/{session_id}/messages", response_model=list[ConversationTurn])
async def list_session_turns(
    session_id: str,
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = None,
    after_id: int | None = None,
):
    return await composite.aget_messages(
        session_id=session_id, limit=limit, before_id=before_id, after_id=after_id
    )


@app.post("/v1/users/{user_id}/facts")
async def store_user_fact(user_id: str, fact: str, memory_type: str = "preference"):
    await composite.acreate_memory(fact=fact, user_id=user_id, memory_type=memory_type)
    return {"status": "persisted"}


@app.get("/v1/users/{user_id}/facts", response_model=list[MemoryItem])
async def search_user_facts(user_id: str, query: str | None = None, limit: int = 5):
    return await composite.aretrieve_memories(user_id=user_id, query=query, limit=limit)


@app.delete("/v1/users/{user_id}/facts")
async def purge_user_facts(user_id: str):
    deleted = await composite.apurge_memories(user_id=user_id)
    return {"status": "purged", "count": deleted}
```

---

## 8. Verification & Testing

Run the hermetic unit and contract memory suites:

```bash
pytest tests/unit/test_memory.py tests/contract/test_memory_contract.py --eval-mode=in_memory -v
ruff check sdk/src/agentops_sdk/memory/ tests/unit/test_memory.py tests/contract/test_memory_contract.py
ruff format --check sdk/src/agentops_sdk/memory/ tests/unit/test_memory.py tests/contract/test_memory_contract.py
```
