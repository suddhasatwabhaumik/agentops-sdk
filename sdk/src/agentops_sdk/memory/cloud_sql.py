# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""SQLAlchemy Core relational session memory adapter for Cloud SQL."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import contextlib
import json
import re
import threading
from typing import Any

from typing_extensions import override

try:
    import sqlalchemy
    from sqlalchemy import pool as sa_pool
except ImportError:  # pragma: no cover
    sqlalchemy = None  # type: ignore[assignment]
    sa_pool = None  # type: ignore[assignment]

from agentops_sdk.domain import exceptions
from agentops_sdk.domain import models
from agentops_sdk.ports import memory as memory_ports

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _validate_session_id(session_id: str) -> str:
    cleaned = session_id.strip() if isinstance(session_id, str) else ""
    if not cleaned or len(cleaned) > 256 or "\x00" in cleaned:
        raise ValueError("session_id must be 1-256 non-null characters.")
    return cleaned


def _validate_turn_args(
    session_id: str, role: str, content: str
) -> tuple[str, str]:
    clean_sid = _validate_session_id(session_id)
    clean_role = role.strip() if isinstance(role, str) else ""
    if not clean_role or len(clean_role) > 64 or not isinstance(content, str):
        raise ValueError("role (1-64 chars) and str content are required.")
    return clean_sid, clean_role


def _validate_pagination(limit: int, offset: int) -> int:
    if limit <= 0 or offset < 0:
        raise ValueError("limit must be positive and offset non-negative.")
    return min(limit, MAX_PAGE_SIZE)


def _build_engine(
    connection_url: str, creator: Callable[[], Any] | None
) -> Any:
    if sqlalchemy is None or sa_pool is None:
        raise exceptions.ConfigurationError(
            "Install agentops-sdk[cloudsql] to use CloudSQLSessionMemory."
        )
    clean_url = (
        connection_url.strip() if isinstance(connection_url, str) else ""
    )
    if not clean_url:
        raise exceptions.ConfigurationError(
            "connection_url is required for CloudSQLSessionMemory."
        )
    if clean_url.startswith("sqlite"):
        engine_kwargs: dict[str, Any] = {
            "connect_args": {"check_same_thread": False}
        }
        if ":memory:" in clean_url or clean_url in ("sqlite://", "sqlite:///"):
            engine_kwargs["poolclass"] = sa_pool.StaticPool
    else:
        engine_kwargs = {"pool_pre_ping": True, "pool_recycle": 1800}
    if creator is not None:
        engine_kwargs["creator"] = creator
    try:
        return sqlalchemy.create_engine(clean_url, **engine_kwargs)
    except Exception as exc:
        raise exceptions.SessionMemoryError(
            "Failed to create SQLAlchemy engine for session memory."
        ) from exc


def _define_messages_table(metadata_obj: Any, table_name: str) -> Any:
    if not _TABLE_NAME_RE.fullmatch(table_name):
        raise ValueError(f"Invalid SQL table_name: {table_name!r}")
    return sqlalchemy.Table(
        table_name,
        metadata_obj,
        sqlalchemy.Column(
            "id", sqlalchemy.Integer, primary_key=True, autoincrement=True
        ),
        sqlalchemy.Column("session_id", sqlalchemy.String(256), nullable=False),
        sqlalchemy.Column("role", sqlalchemy.String(64), nullable=False),
        sqlalchemy.Column("content", sqlalchemy.Text, nullable=False),
        sqlalchemy.Column("timestamp", sqlalchemy.String(64), nullable=False),
        sqlalchemy.Column(
            "metadata_json", sqlalchemy.Text, nullable=False, default="{}"
        ),
        sqlalchemy.Index(f"ix_{table_name}_session_id_id", "session_id", "id"),
    )


def _row_to_turn(row: Any) -> models.ConversationTurn:
    raw_json = getattr(row, "metadata_json", "{}")
    parsed = json.loads(raw_json) if raw_json else {}
    return models.ConversationTurn(
        id=int(row.id) if row.id is not None else None,
        session_id=str(row.session_id),
        role=str(row.role),
        content=str(row.content),
        timestamp=str(row.timestamp),
        metadata=parsed if isinstance(parsed, dict) else {},
    )


class CloudSQLSessionMemory(memory_ports.SessionMemoryPort):
    """Relational session memory adapter backed by SQLAlchemy Core."""

    def __init__(
        self,
        connection_url: str,
        *,
        creator: Callable[[], Any] | None = None,
        table_name: str = "agentops_session_messages",
        auto_create_schema: bool = True,
    ) -> None:
        if not isinstance(table_name, str) or not _TABLE_NAME_RE.fullmatch(
            table_name
        ):
            raise ValueError(f"Invalid SQL table_name: {table_name!r}")
        self._engine = _build_engine(connection_url, creator)
        clean_url = connection_url.strip().lower()
        is_sqlite_or_static = clean_url.startswith("sqlite") or (
            sa_pool is not None
            and isinstance(
                getattr(self._engine, "pool", None), sa_pool.StaticPool
            )
        )
        self._db_lock: threading.RLock | None = (
            threading.RLock() if is_sqlite_or_static else None
        )
        self._metadata = sqlalchemy.MetaData()
        self._table = _define_messages_table(self._metadata, table_name)
        if auto_create_schema:
            try:
                with self._op_lock():
                    self._metadata.create_all(self._engine)
            except Exception as exc:
                self._engine.dispose()
                raise exceptions.SessionMemoryError(
                    "Failed to initialize session memory database schema."
                ) from exc

    def _op_lock(self) -> contextlib.AbstractContextManager[Any]:
        return (
            self._db_lock
            if self._db_lock is not None
            else contextlib.nullcontext()
        )

    def close(self) -> None:
        """Disposes the underlying SQLAlchemy connection pool."""
        with self._op_lock():
            self._engine.dispose()

    def __enter__(self) -> CloudSQLSessionMemory:
        return self

    def __exit__(self, exc_t: Any, exc_v: Any, tb: Any) -> None:
        del exc_t, exc_v, tb
        self.close()

    @override
    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> models.ConversationTurn:
        clean_sid, clean_role = _validate_turn_args(session_id, role, content)
        meta_dict = dict(metadata or {})
        ts = models.utc_now_iso()
        stmt = self._table.insert().values(
            session_id=clean_sid,
            role=clean_role,
            content=content,
            timestamp=ts,
            metadata_json=json.dumps(meta_dict),
        )
        try:
            with self._op_lock(), self._engine.begin() as conn:
                pk = conn.execute(stmt).inserted_primary_key
                row_id = int(pk[0]) if pk and pk[0] is not None else None
        except Exception as exc:
            raise exceptions.SessionMemoryError(
                f"Failed to append turn for session_id={clean_sid!r}."
            ) from exc
        return models.ConversationTurn(
            id=row_id,
            session_id=clean_sid,
            role=clean_role,
            content=content,
            timestamp=ts,
            metadata=meta_dict,
        )

    @override
    def get_messages(
        self,
        session_id: str,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        after_id: int | None = None,
        before_id: int | None = None,
    ) -> list[models.ConversationTurn]:
        clean_sid = _validate_session_id(session_id)
        effective_limit = _validate_pagination(limit, offset)
        cols = self._table.c
        stmt = sqlalchemy.select(self._table).where(
            cols.session_id == clean_sid
        )
        if after_id is not None:
            stmt = stmt.where(cols.id > after_id)
        if before_id is not None:
            stmt = stmt.where(cols.id < before_id)
        stmt = (
            stmt.order_by(cols.id.asc()).offset(offset).limit(effective_limit)
        )
        try:
            with self._op_lock(), self._engine.connect() as conn:
                return [_row_to_turn(r) for r in conn.execute(stmt).fetchall()]
        except Exception as exc:
            raise exceptions.SessionMemoryError(
                f"Failed to fetch turns for session_id={clean_sid!r}."
            ) from exc

    @override
    def clear_session(self, session_id: str) -> int:
        clean_sid = _validate_session_id(session_id)
        stmt = self._table.delete().where(self._table.c.session_id == clean_sid)
        try:
            with self._op_lock(), self._engine.begin() as conn:
                return int(conn.execute(stmt).rowcount or 0)
        except Exception as exc:
            raise exceptions.SessionMemoryError(
                f"Failed to clear turns for session_id={clean_sid!r}."
            ) from exc

    @override
    async def aadd_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> models.ConversationTurn:
        return await asyncio.to_thread(
            self.add_message, session_id, role, content, metadata
        )

    @override
    async def aget_messages(
        self,
        session_id: str,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        after_id: int | None = None,
        before_id: int | None = None,
    ) -> list[models.ConversationTurn]:
        return await asyncio.to_thread(
            self.get_messages,
            session_id,
            limit=limit,
            offset=offset,
            after_id=after_id,
            before_id=before_id,
        )

    @override
    async def aclear_session(self, session_id: str) -> int:
        return await asyncio.to_thread(self.clear_session, session_id)
