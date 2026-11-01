from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import (
    Column, DateTime, ForeignKey, Index, Integer, JSON, MetaData, String, Table,
    Text, UniqueConstraint, delete, insert, select, update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.engine import make_url


MEMORY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_CONTENT_CHARS = 4_000_000
MAX_ENTRY_BYTES = 256 * 1024
MAX_ENTRIES = 1000

memory_metadata = MetaData()
memories = Table(
    "ensemble_memories",
    memory_metadata,
    Column("name", String(128), primary_key=True),
    Column("content", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
entries = Table(
    "ensemble_memory_entries",
    memory_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(128), ForeignKey("ensemble_memories.name", ondelete="CASCADE"), nullable=False),
    Column("event_id", String(256), nullable=True),
    Column("payload_sha256", String(64), nullable=True),
    Column("payload", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("name", "event_id", name="uq_ensemble_memory_event"),
)
Index("ix_ensemble_memory_entries_name_created", entries.c.name, entries.c.created_at)


def validate_memory_name(name: Any) -> str:
    if not isinstance(name, str) or not MEMORY_NAME_PATTERN.fullmatch(name) or name in {".", ".."}:
        raise ValueError("memory name must contain 1-128 safe characters")
    return name


def _canonical_payload(payload: dict[str, Any]) -> tuple[str, int]:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("entry must contain JSON-serializable values") from exc
    size = len(encoded.encode("utf-8"))
    if size > MAX_ENTRY_BYTES:
        raise OverflowError(f"entry exceeds {MAX_ENTRY_BYTES} bytes")
    return encoded, size


class MemoryStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def check_ready(self) -> None: ...
    async def read(self, name: str) -> str | None: ...
    async def write(self, name: str, content: str) -> None: ...
    async def append(self, name: str, payload: dict[str, Any], event_id: str | None = None) -> dict[str, str]: ...
    async def list_names(self) -> list[str]: ...
    async def read_entries(self, name: str, limit: int = MAX_ENTRIES) -> list[dict[str, Any]]: ...
    async def search(self, keywords: list[str], limit: int = 50) -> list[dict[str, Any]]: ...


class SqliteMemoryStore:
    """SQLite-backed episodic memory and append-only event ledger."""

    def __init__(self, database_url: str | None = None):
        url_text = database_url or "sqlite+aiosqlite:///./.ensemble/ensemble.db"
        url = make_url(url_text)
        if url.get_backend_name() != "sqlite":
            raise ValueError("Ensemble memory supports SQLite persistence only")
        self.engine: AsyncEngine = create_async_engine(url_text, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            from sqlalchemy import inspect
            names = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        required = {"ensemble_memories", "ensemble_memory_entries"}
        if not required.issubset(names):
            raise RuntimeError("Memory schema is not initialized; run 'alembic upgrade head'")

    async def close(self) -> None:
        await self.engine.dispose()

    async def check_ready(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(select(1))
            await connection.execute(select(memories.c.name).limit(1))

    async def read(self, name: str) -> str | None:
        validate_memory_name(name)
        async with self.engine.connect() as connection:
            return await connection.scalar(select(memories.c.content).where(memories.c.name == name))

    async def write(self, name: str, content: str) -> None:
        validate_memory_name(name)
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if len(content) > MAX_CONTENT_CHARS:
            raise OverflowError(f"content exceeds {MAX_CONTENT_CHARS} characters")
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            await connection.execute(
                sqlite_insert(memories)
                .values(name=name, content=content, updated_at=now)
                .on_conflict_do_update(
                    index_elements=[memories.c.name],
                    set_={"content": content, "updated_at": now},
                )
            )

    async def append(self, name: str, payload: dict[str, Any], event_id: str | None = None) -> dict[str, str]:
        validate_memory_name(name)
        if not isinstance(payload, dict) or not payload:
            raise ValueError("entry must be a non-empty JSON object")
        record = dict(payload)
        if event_id is not None:
            if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 256:
                raise ValueError("event_id must be a non-empty string of at most 256 characters")
            if "event_id" in record and record["event_id"] != event_id:
                raise ValueError("entry.event_id must match event_id")
            if {"payload_sha256", "captured_at"}.intersection(record):
                raise ValueError("entry contains reserved Memory metadata fields")
            record["event_id"] = event_id
            canonical, _ = _canonical_payload(record)
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        else:
            digest = None
        _canonical_payload(record)
        now = datetime.now(timezone.utc)
        record.setdefault("timestamp", now.isoformat().replace("+00:00", "Z"))
        async with self.engine.begin() as connection:
            await connection.execute(
                sqlite_insert(memories)
                .values(name=name, content="", updated_at=now)
                .on_conflict_do_nothing(index_elements=[memories.c.name])
            )
            if event_id is None:
                await connection.execute(insert(entries).values(
                    name=name, event_id=None, payload_sha256=None, payload=record, created_at=now
                ))
                return {"status": "stored"}
            statement = sqlite_insert(entries).values(
                name=name, event_id=event_id, payload_sha256=digest, payload=record, created_at=now
            ).on_conflict_do_nothing(index_elements=[entries.c.name, entries.c.event_id])
            result = await connection.execute(statement)
            if result.rowcount == 1:
                return {"status": "stored", "event_id": event_id}
            existing = (await connection.execute(
                select(entries.c.payload_sha256).where(
                    entries.c.name == name, entries.c.event_id == event_id
                )
            )).scalar_one()
            if existing != digest:
                raise ValueError("event_id already exists with different payload")
            return {"status": "duplicate", "event_id": event_id}

    async def read_entries(self, name: str, limit: int = MAX_ENTRIES) -> list[dict[str, Any]]:
        validate_memory_name(name)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_ENTRIES:
            raise ValueError(f"limit must be an integer between 1 and {MAX_ENTRIES}")
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                select(entries.c.payload).where(entries.c.name == name)
                .order_by(entries.c.id.desc()).limit(limit)
            )).all()
        return [row[0] for row in reversed(rows)]

    async def list_names(self) -> list[str]:
        async with self.engine.connect() as connection:
            names = (await connection.execute(
                select(memories.c.name).order_by(memories.c.name)
            )).scalars().all()
        return list(names)

    async def search(self, keywords: list[str], limit: int = 50) -> list[dict[str, Any]]:
        if not isinstance(keywords, list) or any(not isinstance(word, str) for word in keywords):
            raise ValueError("keywords must be a list of strings")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        terms = list(dict.fromkeys(word.casefold() for word in keywords if word.strip()))
        if not terms:
            return []
        async with self.engine.connect() as connection:
            docs = (await connection.execute(select(memories.c.name, memories.c.content))).all()
            event_rows = (await connection.execute(select(entries.c.name, entries.c.payload))).all()
        candidates: dict[str, str] = {name: content for name, content in docs}
        for name, payload in event_rows:
            candidates[name] = candidates.get(name, "") + "\n" + json.dumps(payload, ensure_ascii=False)
        results = []
        for name, content in candidates.items():
            folded = content.casefold()
            matched = [term for term in terms if term in folded]
            if matched:
                results.append({"name": name, "score": len(matched) / len(terms), "matched": matched})
        return sorted(results, key=lambda item: (-item["score"], item["name"]))[:limit]

    async def retrieve(self, name: str, context: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
        validate_memory_name(name)
        if not isinstance(context, dict):
            raise ValueError("context must be a mapping")
        if not isinstance(context.get("request_id"), str) or not isinstance(context.get("objective"), str):
            raise ValueError("context requires request_id and objective")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        current_id = context.get("execution_id")
        parent_id = context.get("parent_execution_id")
        lineage = context.get("lineage", [])
        if not isinstance(lineage, (list, tuple)) or any(not isinstance(item, str) for item in lineage):
            raise ValueError("context.lineage must be a list of strings")
        records = await self.read_entries(name)
        matches = []
        for index, record in enumerate(records):
            prior = record.get("execution_context")
            if not isinstance(prior, dict):
                continue
            prior_id = prior.get("execution_id")
            prior_lineage = prior.get("lineage", [])
            if not prior_id or prior_id == current_id or prior.get("request_id") != context["request_id"]:
                continue
            relation = None
            rank = 0
            if parent_id and prior_id == parent_id:
                relation, rank = "parent", 100
            elif prior_lineage and len(prior_lineage) < len(lineage) and list(lineage[:len(prior_lineage)]) == list(prior_lineage) and prior_id == prior_lineage[-1]:
                relation, rank = "ancestor", 90
            elif prior_lineage and lineage and prior_lineage[0] == lineage[0]:
                relation, rank = "related-lineage", 60
            else:
                relation, rank = "same-request", 70
            matches.append({
                "record": record, "execution_context": prior,
                "relation": relation, "authoritative": False, "_rank": rank, "_index": index
            })
        matches.sort(key=lambda item: (-item["_rank"], item["_index"]))
        for item in matches:
            item.pop("_rank", None)
            item.pop("_index", None)
        return matches[:limit]

    async def chunk(self, name: str, size: int = 500) -> list[dict[str, Any]]:
        content = await self.read(name)
        if content is None:
            return []
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 10000:
            raise ValueError("size must be an integer between 1 and 10000")
        return [
            {"ordinal": offset // size, "start": offset, "end": min(offset + size, len(content)),
             "content": content[offset:offset + size]}
            for offset in range(0, len(content), size)
        ]
