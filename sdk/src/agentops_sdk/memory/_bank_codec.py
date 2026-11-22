# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Internal codec and context helpers for memory adapters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import datetime
from typing import Any

from agentops_sdk.domain import models

_META_KEYS = ("bool_value", "double_value", "string_value", "timestamp_value")


def field(obj: Any, key: str) -> Any:
    """Reads a key from a mapping or an attribute from an object."""
    return obj.get(key) if isinstance(obj, Mapping) else getattr(obj, key, None)


def encode_val(val: Any) -> dict[str, Any]:
    """Encodes a Python metadata value into Vertex Memory Bank format."""
    if isinstance(val, Mapping) and any(k in val for k in _META_KEYS):
        return dict(val)
    if isinstance(val, bool | datetime.datetime):
        key = "bool_value" if isinstance(val, bool) else "timestamp_value"
        return {key: val}
    key = "double_value" if isinstance(val, int | float) else "string_value"
    return {key: float(val) if isinstance(val, int | float) else str(val)}


def decode_val(val: Any) -> Any:
    """Decodes a Vertex Memory Bank metadata value into a Python primitive."""
    for item in (val, *(field(val, k) for k in _META_KEYS)):
        if isinstance(item, datetime.datetime):
            return item.isoformat()
        if isinstance(item, bool | int | float | str):
            return item
    return str(val)


def _extract_inner(raw: Any) -> Any:
    for attr in ("memory", "response"):
        cand = field(raw, attr)
        if any(isinstance(field(cand, fld), str) for fld in ("fact", "name")):
            return cand
    return raw


def _extract_score(raw: Any) -> float | None:
    score = field(raw, "score")
    if not isinstance(score, int | float) or isinstance(score, bool):
        score = field(raw, "distance")
    if isinstance(score, int | float) and not isinstance(score, bool):
        return float(score)
    return None


def to_item(
    raw: Any,
    user_id: str,
    fact: str = "",
    memory_type: str = "fact",
    metadata: Any = None,
) -> models.MemoryItem:
    """Converts a raw Vertex Memory Bank response into a MemoryItem."""
    inner = _extract_inner(raw)
    raw_meta = field(inner, "metadata")
    pairs = raw_meta.items() if isinstance(raw_meta, Mapping) else ()
    decoded = {str(k): decode_val(v) for k, v in pairs}
    decoded_type = decoded.pop("memory_type", None)
    raw_id = field(inner, "name") or field(raw, "name")
    raw_fact = field(inner, "fact")
    ctime = field(inner, "create_time")
    ts = ctime.isoformat() if isinstance(ctime, datetime.datetime) else ctime
    return models.MemoryItem(
        memory_id=raw_id if isinstance(raw_id, str) else "",
        user_id=user_id,
        fact=raw_fact if isinstance(raw_fact, str) and raw_fact else fact,
        memory_type=(
            decoded_type
            if isinstance(decoded_type, str) and decoded_type
            else memory_type
        ),
        score=_extract_score(raw),
        created_at=ts if isinstance(ts, str) and ts else models.utc_now_iso(),
        metadata=decoded or dict(metadata or {}),
    )


def clean_memory_type(memory_type: str | None) -> str | None:
    """Normalizes an optional memory_type filter string."""
    cleaned = memory_type.strip() if isinstance(memory_type, str) else ""
    return None if not cleaned or cleaned == "all" else cleaned


def keep_memory_item(item: models.MemoryItem, filter_type: str | None) -> bool:
    """Returns True when an item has a fact and matches filter_type."""
    return bool(item.fact) and (
        filter_type is None or item.memory_type == filter_type
    )


def parse_purge_count(op: Any) -> int:
    """Extracts a non-negative purge count from a Memory Bank response."""
    resp = field(op, "response") or op
    for val in (op, field(resp, "purge_count"), field(op, "purge_count")):
        if isinstance(val, int) and not isinstance(val, bool):
            return max(0, val)
    return 0


def parse_generated(op: Any, user_id: str) -> list[models.MemoryItem]:
    """Parses generated memories from a Memory Bank operation response."""
    resp = field(op, "response") or op
    rows = field(resp, "generated_memories")
    if isinstance(rows, str | bytes | Mapping) or not isinstance(
        rows, Iterable
    ):
        rows = op if isinstance(op, Sequence) else ()
    return [item for entry in rows if (item := to_item(entry, user_id)).fact]


def build_context(
    session_id: str,
    user_id: str,
    turns: list[models.ConversationTurn],
    memories: list[models.MemoryItem],
) -> dict[str, Any]:
    """Builds the composite memory context dictionary."""
    short_term = [{"role": t.role, "content": t.content} for t in turns]
    return {
        "session_id": session_id,
        "user_id": user_id,
        "turns": turns,
        "memories": memories,
        "short_term_history": short_term,
        "long_term_memories": [m.model_dump() for m in memories],
    }
