from __future__ import annotations

import hashlib
import json
import os
import re
import hashlib
from pathlib import Path
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
memory_sources = Table(
    "ensemble_memory_sources",
    memory_metadata,
    Column("source_path", String(4096), primary_key=True),
    Column("document_name", String(128), ForeignKey("ensemble_memories.name", ondelete="CASCADE"), nullable=False, unique=True),
    Column("source", String(64), nullable=False),
    Column("scope", String(4096), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("stale", Integer, nullable=False, default=0),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
Index("ix_ensemble_memory_sources_scope_stale", memory_sources.c.scope, memory_sources.c.stale)


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



def _validate_ingest(source_path: Any, content: Any, sha256: Any, metadata: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(source_path, str) or not Path(source_path).is_absolute() or len(source_path) > 4096:
        raise ValueError("source_path must be an absolute path")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if len(content) > MAX_CONTENT_CHARS:
        raise OverflowError(f"content exceeds {MAX_CONTENT_CHARS} characters")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("sha256 must be a lowercase SHA-256 digest")
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != sha256:
        raise ValueError("sha256 does not match content")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    scope = metadata.get("scope")
    if not isinstance(scope, str) or not Path(scope).is_absolute() or len(scope) > 4096:
        raise ValueError("metadata.scope must be an absolute path")
    if not Path(source_path).is_relative_to(Path(scope)):
        raise ValueError("source_path must be within metadata.scope")
    _canonical_payload(metadata)
    return scope, dict(metadata)


def _validate_reconciliation(source: Any, scope: Any, paths: Any) -> set[str]:
    if source != "claude-code":
        raise ValueError("unsupported reconciliation source")
    if not isinstance(scope, str) or not Path(scope).is_absolute() or len(scope) > 4096:
        raise ValueError("scope must be an absolute path")
    if not isinstance(paths, list) or len(paths) > 10000:
        raise ValueError("paths must be a list with at most 10000 entries")
    current = set()
    for path in paths:
        if not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).is_relative_to(Path(scope)):
            raise ValueError("every path must be absolute and within scope")
        current.add(path)
    return current


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
    async def ingest_claude_markdown(self, source_path: str, content: str, sha256: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]: ...
    async def reconcile_claude_markdown(self, source: str, scope: str, paths: list[str]) -> dict[str, Any]: ...
    async def knowledge_status(self) -> dict[str, Any]: ...


class SqliteMemoryStore:
    """SQLite-backed episodic memory and append-only event ledger."""

    def __init__(self, database_url: str | None = None):
        url_text = database_url or os.getenv("ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db")
        url = make_url(url_text)
        if url.get_backend_name() != "sqlite":
            raise ValueError("Ensemble memory supports SQLite persistence only")
        self.engine: AsyncEngine = create_async_engine(url_text, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            from sqlalchemy import inspect
            names = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        required = {"ensemble_memories", "ensemble_memory_entries", "ensemble_memory_sources"}
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

    async def ingest_claude_markdown(
        self, source_path: str, content: str, sha256: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        scope, meta = _validate_ingest(source_path, content, sha256, metadata)
        document_name = "claude-code-" + hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:32]
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            existing = (await connection.execute(
                select(memory_sources.c.sha256, memory_sources.c.stale)
                .where(memory_sources.c.source_path == source_path)
            )).first()
            stored = await connection.scalar(select(memories.c.content).where(memories.c.name == document_name))
            if existing and existing.sha256 == sha256 and not existing.stale and stored is not None:
                if hashlib.sha256(stored.encode("utf-8")).hexdigest() == sha256:
                    return {"status": "unchanged", "source_path": source_path, "sha256": sha256}
            await connection.execute(
                sqlite_insert(memories).values(name=document_name, content=content, updated_at=now)
                .on_conflict_do_update(
                    index_elements=[memories.c.name],
                    set_={"content": content, "updated_at": now},
                )
            )
            await connection.execute(
                sqlite_insert(memory_sources).values(
                    source_path=source_path, document_name=document_name, source="claude-code",
                    scope=scope, sha256=sha256, metadata=meta, stale=False, updated_at=now,
                ).on_conflict_do_update(
                    index_elements=[memory_sources.c.source_path],
                    set_={"document_name": document_name, "source": "claude-code", "scope": scope,
                          "sha256": sha256, "metadata": meta, "stale": False, "updated_at": now},
                )
            )
        return {"status": "ingested", "source_path": source_path, "sha256": sha256, "document": document_name}

    async def reconcile_claude_markdown(self, source: str, scope: str, paths: list[str]) -> dict[str, Any]:
        current = _validate_reconciliation(source, scope, paths)
        async with self.engine.begin() as connection:
            rows = (await connection.execute(
                select(memory_sources.c.source_path).where(
                    memory_sources.c.source == source, memory_sources.c.scope == scope,
                    memory_sources.c.stale.is_(False),
                )
            )).scalars().all()
            stale = sorted(path for path in rows if path not in current)
            if stale:
                await connection.execute(
                    update(memory_sources).where(memory_sources.c.source_path.in_(stale))
                    .values(stale=True, updated_at=datetime.now(timezone.utc))
                )
        return {"status": "reconciled", "stale": stale, "count": len(stale)}

    async def knowledge_status(self) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            count = await connection.scalar(
                select(entries.c.id.count()).select_from(memory_sources.join(
                    memories, memory_sources.c.document_name == memories.c.name
                )).where(memory_sources.c.source == "claude-code", memory_sources.c.stale.is_(False))
            )
        indexed = int(count or 0)
        return {"source": "claude-code", "status": "available" if indexed else "empty",
                "indexed_document_count": indexed}

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
            elif prior_lineage and len(prior_lineage) > len(lineage) and list(prior_lineage[:len(lineage)]) == list(lineage) and prior_id == prior_lineage[-1]:
                continue
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


class InMemoryMemoryStore:
    """Isolated non-durable adapter used by tests."""

    def __init__(self):
        self._memories: dict[str, str] = {}
        self._entries: dict[str, list[dict[str, Any]]] = {}
        self._events: dict[tuple[str, str], str] = {}
        self.sources: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def check_ready(self) -> None:
        return None

    async def read(self, name: str) -> str | None:
        return self._memories.get(validate_memory_name(name))

    async def write(self, name: str, content: str) -> None:
        validate_memory_name(name)
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if len(content) > MAX_CONTENT_CHARS:
            raise OverflowError(f"content exceeds {MAX_CONTENT_CHARS} characters")
        self._memories[name] = content

    async def append(self, name: str, payload: dict[str, Any], event_id: str | None = None) -> dict[str, str]:
        validate_memory_name(name)
        if not isinstance(payload, dict) or not payload:
            raise ValueError("entry must be a non-empty JSON object")
        record = dict(payload)
        digest = None
        if event_id is not None:
            if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 256:
                raise ValueError("event_id must be a non-empty string of at most 256 characters")
            record["event_id"] = event_id
            canonical, _ = _canonical_payload(record)
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            existing = self._events.get((name, event_id))
            if existing is not None:
                if existing != digest:
                    raise ValueError("event_id already exists with different payload")
                return {"status": "duplicate", "event_id": event_id}
        _canonical_payload(record)
        record.setdefault("timestamp", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        self._memories.setdefault(name, "")
        self._entries.setdefault(name, []).append(record)
        if event_id is not None:
            self._events[(name, event_id)] = digest
        return {"status": "stored", **({"event_id": event_id} if event_id else {})}

    async def read_entries(self, name: str, limit: int = MAX_ENTRIES) -> list[dict[str, Any]]:
        validate_memory_name(name)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_ENTRIES:
            raise ValueError(f"limit must be an integer between 1 and {MAX_ENTRIES}")
        return list(self._entries.get(name, [])[-limit:])

    async def list_names(self) -> list[str]:
        return sorted(set(self._memories) | set(self._entries))

    async def search(self, keywords: list[str], limit: int = 50) -> list[dict[str, Any]]:
        if not isinstance(keywords, list) or any(not isinstance(word, str) for word in keywords):
            raise ValueError("keywords must be a list of strings")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        terms = list(dict.fromkeys(word.casefold() for word in keywords if word.strip()))
        results = []
        for name in await self.list_names():
            content = self._memories.get(name, "") + json.dumps(self._entries.get(name, []), ensure_ascii=False)
            matched = [term for term in terms if term in content.casefold()]
            if matched:
                results.append({"name": name, "score": len(matched) / len(terms), "matched": matched})
        return sorted(results, key=lambda item: (-item["score"], item["name"]))[:limit]

    async def ingest_claude_markdown(
        self, source_path: str, content: str, sha256: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        scope, meta = _validate_ingest(source_path, content, sha256, metadata)
        document_name = "claude-code-" + hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:32]
        existing = self.sources.get(source_path)
        stored = self._memories.get(document_name)
        if existing and existing["sha256"] == sha256 and not existing["stale"] and stored is not None:
            if hashlib.sha256(stored.encode("utf-8")).hexdigest() == sha256:
                return {"status": "unchanged", "source_path": source_path, "sha256": sha256}
        now = datetime.now(timezone.utc).isoformat()
        self._memories[document_name] = content
        self.sources[source_path] = {
            "source_path": source_path, "document_name": document_name, "source": "claude-code",
            "scope": scope, "sha256": sha256, "metadata": meta, "stale": False, "updated_at": now,
        }
        return {"status": "ingested", "source_path": source_path, "sha256": sha256, "document": document_name}

    async def reconcile_claude_markdown(self, source: str, scope: str, paths: list[str]) -> dict[str, Any]:
        current = _validate_reconciliation(source, scope, paths)
        stale = []
        for source_path, entry in self.sources.items():
            if entry["source"] == source and entry["scope"] == scope and not entry["stale"] and source_path not in current:
                entry["stale"] = True
                entry["updated_at"] = datetime.now(timezone.utc).isoformat()
                stale.append(source_path)
        stale.sort()
        return {"status": "reconciled", "stale": stale, "count": len(stale)}

    async def knowledge_status(self) -> dict[str, Any]:
        indexed = sum(
            1 for entry in self.sources.values()
            if entry["source"] == "claude-code" and not entry["stale"]
            and entry["document_name"] in self._memories
        )
        return {"source": "claude-code", "status": "available" if indexed else "empty",
                "indexed_document_count": indexed}

    async def retrieve(self, name: str, context: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
        validate_memory_name(name)
        if not isinstance(context, dict) or not isinstance(context.get("request_id"), str) or not isinstance(context.get("objective"), str):
            raise ValueError("context requires request_id and objective")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        lineage = context.get("lineage", [])
        matches = []
        for index, record in enumerate(self._entries.get(name, [])):
            prior = record.get("execution_context")
            if not isinstance(prior, dict) or prior.get("request_id") != context["request_id"]:
                continue
            prior_id, prior_lineage = prior.get("execution_id"), prior.get("lineage", [])
            if not prior_id or prior_id == context.get("execution_id"):
                continue
            if context.get("parent_execution_id") == prior_id:
                relation, rank = "parent", 100
            elif prior_lineage and len(prior_lineage) < len(lineage) and list(lineage[:len(prior_lineage)]) == list(prior_lineage) and prior_id == prior_lineage[-1]:
                relation, rank = "ancestor", 90
            elif prior_lineage and lineage and prior_lineage[0] == lineage[0]:
                relation, rank = "related-lineage", 60
            else:
                relation, rank = "same-request", 70
            matches.append({"record": record, "execution_context": prior, "relation": relation, "authoritative": False, "_rank": rank, "_index": index})
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
        return [{"ordinal": offset // size, "start": offset, "end": min(offset + size, len(content)), "content": content[offset:offset + size]} for offset in range(0, len(content), size)]
