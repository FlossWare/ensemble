from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, MetaData,
    String, Table, Text, UniqueConstraint, delete, func, insert, select, update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.engine import make_url

from .partitioning import PartitionedAsyncEngine
from .ranking import fuse as rrf_fuse


MEMORY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_CONTENT_CHARS = 4_000_000
MAX_ENTRY_BYTES = 256 * 1024
MAX_ENTRIES = 1000

memory_metadata = MetaData()
memories = Table(
    "ensemble_memories", memory_metadata,
    Column("name", String(128), primary_key=True),
    Column("content", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
entries = Table(
    "ensemble_memory_entries", memory_metadata,
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
    "ensemble_memory_sources", memory_metadata,
    Column("source_path", String(4096), primary_key=True),
    Column("document_name", String(128), ForeignKey("ensemble_memories.name", ondelete="CASCADE"), nullable=False, unique=True),
    Column("source", String(64), nullable=False),
    Column("scope", String(4096), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("stale", Boolean, nullable=False, default=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
Index("ix_ensemble_memory_sources_scope_stale", memory_sources.c.scope, memory_sources.c.stale)

memory_generation_records = Table(
    "ensemble_memory_generation_records", memory_metadata,
    Column("generation_id", String(64), primary_key=True),
    Column("name", String(128), primary_key=True),
    Column("snapshot", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
memory_generation_changes = Table(
    "ensemble_memory_generation_changes", memory_metadata,
    Column("change_id", Integer, primary_key=True, autoincrement=True),
    Column("generation_id", String(64), nullable=False),
    Column("name", String(128), nullable=False),
    Column("snapshot", JSON, nullable=True),
    Column("acknowledged", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
Index("ix_memory_generation_changes_pending", memory_generation_changes.c.generation_id,
      memory_generation_changes.c.acknowledged, memory_generation_changes.c.change_id)


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


def _validate_expiry(record: dict[str, Any]) -> None:
    if "expires_at" not in record:
        return
    value = record["expires_at"]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expires_at must be an ISO-8601 timestamp with a timezone")
    try:
        expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("expires_at must be an ISO-8601 timestamp with a timezone") from exc
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise ValueError("expires_at must include a timezone")
    record["expires_at"] = expires_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _entry_is_expired(record: dict[str, Any], now: datetime | None = None) -> bool:
    value = record.get("expires_at")
    if value is None:
        return False
    try:
        expires_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            return True
        return expires_at <= (now or datetime.now(timezone.utc))
    except (TypeError, ValueError):
        # Invalid historical expiry metadata is excluded from recall, not treated
        # as indefinitely valid.
        return True


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
    resolved_scope = Path(scope).resolve(strict=False)
    resolved_source = Path(source_path).resolve(strict=False)
    if str(resolved_scope) != scope or str(resolved_source) != source_path:
        raise ValueError("scope and source_path must use canonical paths without traversal or symlink aliases")
    if not resolved_source.is_relative_to(resolved_scope):
        raise ValueError("source_path must be within metadata.scope")
    _canonical_payload(metadata)
    return scope, dict(metadata)


def _validate_reconciliation(source: Any, scope: Any, paths: Any) -> set[str]:
    if source != "claude-code":
        raise ValueError("unsupported reconciliation source")
    if not isinstance(scope, str) or not Path(scope).is_absolute() or len(scope) > 4096:
        raise ValueError("scope must be an absolute path")
    resolved_scope = Path(scope).resolve(strict=False)
    if str(resolved_scope) != scope:
        raise ValueError("scope must use a canonical path")
    if not isinstance(paths, list) or len(paths) > 10000:
        raise ValueError("paths must be a list with at most 10000 entries")
    current = set()
    for path in paths:
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError("every path must be absolute and within scope")
        resolved_path = Path(path).resolve(strict=False)
        if str(resolved_path) != path or not resolved_path.is_relative_to(resolved_scope):
            raise ValueError("every path must be canonical and within scope")
        current.add(path)
    return current


def _vectorize_text(text: str) -> dict[str, float]:
    terms = []
    for token in re.findall(r"[^\W_]+(?:[._:-][^\W_]+)*", text.casefold(), re.UNICODE):
        if len(token) > 2:
            terms.append(token)
        terms.extend(part for part in re.split(r"[._:-]+", token) if len(part) > 2)
    counts: dict[str, int] = {}
    for term in terms:
        counts[term] = counts.get(term, 0) + 1
    length = max(1, len(terms))
    return {term: count / length for term, count in counts.items()}


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    terms = set(left) | set(right)
    dot = sum(left.get(term, 0.0) * right.get(term, 0.0) for term in terms)
    norm_left = math.sqrt(sum(value * value for value in left.values()))
    norm_right = math.sqrt(sum(value * value for value in right.values()))
    return dot / (norm_left * norm_right) if norm_left and norm_right else 0.0


def _chunk_markdown(content: str, max_chunk_size: int = 2000) -> list[dict[str, Any]]:
    lines = content.splitlines(keepends=True)
    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    header, start_line, size = "intro", 0, 0
    for line_number, line in enumerate(lines):
        if line.startswith("#"):
            if current:
                value = "".join(current)
                chunks.append({"header": header, "start_line": start_line, "end_line": line_number,
                               "content": value, "size": len(value)})
            header, current, start_line, size = line.lstrip("#").strip(), [line], line_number, len(line)
            continue
        current.append(line)
        size += len(line)
        if size > max_chunk_size:
            value = "".join(current)
            chunks.append({"header": header, "start_line": start_line, "end_line": line_number + 1,
                           "content": value, "size": len(value)})
            current, size, start_line = [], 0, line_number + 1
    if current:
        value = "".join(current)
        chunks.append({"header": header, "start_line": start_line, "end_line": len(lines),
                       "content": value, "size": len(value)})
    return chunks



def _memory_rrf_constant() -> int:
    try:
        value = int(os.getenv("ENSEMBLE_RRF_K", "60"))
    except (TypeError, ValueError):
        return 60
    return value if 1 <= value <= 10_000 else 60


def _fuse_memory_results(keyword_results, semantic_results, top_k, k=None):
    """Fuse Memory's lexical and semantic rankings without comparing raw scores."""
    k = _memory_rrf_constant() if k is None else k
    ranked = {"keyword": [], "semantic": []}
    identities, raw, ranks = {}, {}, {}
    for channel, results in (("keyword", keyword_results), ("semantic", semantic_results)):
        for rank, item in enumerate(results, 1):
            name, section = item["file"], item.get("section", "full")
            key = json.dumps([name, section], ensure_ascii=False, separators=(",", ":"))
            identities[key] = (name, section)
            raw.setdefault(key, {"keyword_score": 0.0, "semantic_score": 0.0})
            raw[key][channel + "_score"] = float(item["score"])
            ranks.setdefault(key, {})[channel] = rank
            ranked[channel].append({"key": key, "rank": rank})
    if not ranked["keyword"] and not ranked["semantic"]:
        return []
    fused = rrf_fuse(ranked, top_k=top_k, k=k)
    return [{
        "file": identities[item["key"]][0],
        "section": identities[item["key"]][1],
        "score": item["score"],
        "keyword_score": raw[item["key"]]["keyword_score"],
        "semantic_score": raw[item["key"]]["semantic_score"],
        "fusion": {
            "algorithm": "rrf", "k": k,
            "ranks": ranks[item["key"]],
            "contributions": item["contributions"],
        },
    } for item in fused]


def _keyword_search(documents: list[tuple[str, str]], active_knowledge: set[str], keywords: list[str], limit: int) -> list[dict[str, Any]]:
    keyword_set = {word.casefold() for word in keywords if isinstance(word, str) and word.strip()}
    if not keyword_set:
        return []
    docs = []
    for name, content in documents:
        if name.startswith("claude-code-") and name not in active_knowledge:
            continue
        for chunk in _chunk_markdown(content) or [{"header": "full", "content": content}]:
            terms = [word.strip(".,!?;:").casefold() for word in chunk["content"].split() if len(word) > 2]
            docs.append((name, chunk, terms))
    count = len(docs)
    if not count:
        return []
    idf = {
        term: math.log(count / max(1, sum(1 for _, _, terms in docs if term in terms)))
        for term in keyword_set
    }
    results = []
    for name, chunk, terms in docs:
        frequencies: dict[str, int] = {}
        for term in terms:
            frequencies[term] = frequencies.get(term, 0) + 1
        matched = [term for term in keyword_set if term in frequencies]
        score = sum((frequencies[term] / max(1, len(terms))) * idf[term] for term in matched)
        if score > 0:
            results.append({
                "file": name, "section": chunk["header"], "score": score,
                "matched_keywords": len(matched), "size_bytes": len(chunk["content"].encode("utf-8")),
            })
    return sorted(results, key=lambda item: (-item["score"], item["file"], item["section"]))[:limit]


class _MemorySearchIndex:
    """Bounded process-local cache of chunks and vectors for Memory documents."""

    def __init__(
        self, max_bytes: int = 32 * 1024 * 1024,
        max_document_bytes: int = 2 * 1024 * 1024, max_entries: int = 512,
    ):
        if max_bytes < 0 or max_document_bytes < 0 or max_entries < 1:
            raise ValueError("Memory search cache limits must be non-negative and max_entries positive")
        self.max_bytes = max_bytes
        self.max_document_bytes = max_document_bytes
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple[str, str], tuple[int, tuple[dict[str, Any], ...]]] = OrderedDict()
        self._locks: dict[tuple[str, str], threading.Lock] = {}
        self._cached_bytes = 0

    def _build(self, content: str) -> tuple[dict[str, Any], ...]:
        chunks = _chunk_markdown(content) or [{"header": "full", "content": content}]
        indexed = []
        for chunk in chunks:
            value = chunk["content"]
            vector = _vectorize_text(value)
            indexed.append({
                "header": chunk["header"],
                "size_bytes": len(value.encode("utf-8")),
                "folded": value.casefold(),
                "vector": vector,
                "magnitude": math.sqrt(sum(weight * weight for weight in vector.values())),
            })
        return tuple(indexed)

    def get(self, name: str, content: str) -> tuple[dict[str, Any], ...]:
        size = len(content.encode("utf-8"))
        if size > self.max_document_bytes or self.max_bytes <= 0:
            return self._build(content)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        key = (name, digest)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached[1]
            build_lock = self._locks.setdefault(key, threading.Lock())
        with build_lock:
            with self._lock:
                cached = self._cache.get(key)
                if cached is not None:
                    self._cache.move_to_end(key)
                    return cached[1]
            indexed = self._build(content)
            with self._lock:
                previous = self._cache.pop(key, None)
                if previous is not None:
                    self._cached_bytes -= previous[0]
                while self._cache and (
                    self._cached_bytes + size > self.max_bytes
                    or len(self._cache) >= self.max_entries
                ):
                    _, (evicted_size, _) = self._cache.popitem(last=False)
                    self._cached_bytes -= evicted_size
                if size <= self.max_bytes:
                    self._cache[key] = (size, indexed)
                    self._cached_bytes += size
                if self._locks.get(key) is build_lock:
                    self._locks.pop(key, None)
            return indexed

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._locks.clear()
            self._cached_bytes = 0


_memory_search_index = _MemorySearchIndex()


def _semantic_search(documents: list[tuple[str, str]], active_knowledge: set[str], query: str, top_k: int, source: str | None) -> list[dict[str, Any]]:
    if not isinstance(query, str) or not query.strip():
        return []
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
        raise ValueError("top_k must be an integer between 1 and 100")
    if source not in (None, "memory", "claude-code"):
        raise ValueError("source must be 'memory' or 'claude-code'")
    normalized = query.strip().casefold().rstrip(".,!?;")
    query_vector = _vectorize_text(normalized)
    if not query_vector:
        return []
    identifier = (
        not any(char.isspace() for char in normalized)
        and (
            bool(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", normalized))
            or ":" in normalized
            or (bool(re.search(r"[-._]", normalized)) and bool(re.search(r"\d", normalized)))
            or normalized.count(".") >= 2
        )
    )
    pattern = re.compile(rf"(?<![\w.-]){re.escape(normalized)}(?![\w.-])", re.UNICODE)
    results = []
    for name, content in documents:
        is_knowledge = name in active_knowledge
        if name.startswith("claude-code-") and not is_knowledge:
            continue
        if source == "claude-code" and not is_knowledge:
            continue
        if source == "memory" and is_knowledge:
            continue
        for chunk in _memory_search_index.get(name, content):
            exact = bool(pattern.search(chunk["folded"])) if identifier else normalized in chunk["folded"]
            if identifier and not exact:
                continue
            score = 1.0 if exact else _cosine(query_vector, chunk["vector"])
            if exact or score > 0.1:
                result = {
                    "file": name, "section": chunk["header"], "score": score,
                    "size_bytes": chunk["size_bytes"], "content": content,
                }
                if is_knowledge:
                    result["source"] = "claude-code"
                results.append(result)
    return sorted(results, key=lambda item: (-item["score"], item["file"], item["section"]))[:top_k]


class MemoryStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def check_ready(self) -> None: ...
    async def read(self, name: str) -> str | None: ...
    async def write(self, name: str, content: str) -> None: ...
    async def delete(self, name: str) -> dict[str, Any]: ...
    async def append(self, name: str, payload: dict[str, Any], event_id: str | None = None) -> dict[str, str]: ...
    async def list_names(self) -> list[str]: ...
    async def read_entries(self, name: str, limit: int = MAX_ENTRIES) -> list[dict[str, Any]]: ...
    async def search(self, keywords: list[str], limit: int = 50) -> list[dict[str, Any]]: ...
    async def search_semantic(self, query: str, top_k: int = 10, source: str | None = None) -> list[dict[str, Any]]: ...
    async def search_hybrid(self, query: str, top_k: int = 10) -> list[dict[str, Any]]: ...
    async def ingest_claude_markdown(self, source_path: str, content: str, sha256: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]: ...
    async def reconcile_claude_markdown(self, source: str, scope: str, paths: list[str]) -> dict[str, Any]: ...
    async def knowledge_status(self) -> dict[str, Any]: ...
    async def retrieve(self, name: str, context: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]: ...
    async def chunk(self, name: str, size: int = 2000) -> list[dict[str, Any]]: ...


class SqliteMemoryStore:
    """SQLite-backed episodic memory, event ledger and imported Knowledge."""

    def __init__(self, database_url: str | None = None):
        url_text = database_url or os.getenv("ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db")
        if make_url(url_text).get_backend_name() != "sqlite":
            raise ValueError("Ensemble memory supports SQLite persistence only")
        self.engine = PartitionedAsyncEngine(url_text, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            from sqlalchemy import inspect
            names = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        required = {"ensemble_memories", "ensemble_memory_entries", "ensemble_memory_sources", "ensemble_memory_generation_records", "ensemble_memory_generation_changes"}
        if not required.issubset(names):
            raise RuntimeError("Memory schema is not initialized; run 'alembic upgrade head'")

    async def close(self) -> None:
        await self.engine.dispose()

    async def _assert_generation_writes_open(self, connection: Any) -> None:
        from sqlalchemy import inspect
        names = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        if "ensemble_index_generation_write_fence" not in names:
            # Lightweight Memory-store unit fixtures may intentionally create
            # only the Memory schema. Production app startup requires migrations.
            return
        from .index_store import generation_write_fence
        fence = (await connection.execute(select(generation_write_fence).where(
            generation_write_fence.c.singleton_id == 1
        ))).mappings().first()
        if fence is not None:
            raise RuntimeError("Memory writes are fenced during generation activation")

    async def _snapshot_for_name(self, connection: Any, name: str) -> dict[str, Any] | None:
        memory = (await connection.execute(select(memories).where(memories.c.name == name))).mappings().first()
        if memory is None:
            return None
        entry_rows = (await connection.execute(
            select(entries.c.payload).where(entries.c.name == name).order_by(entries.c.id)
        )).scalars().all()
        source = (await connection.execute(
            select(memory_sources).where(memory_sources.c.document_name == name)
        )).mappings().first()
        source_snapshot = dict(source) if source else None
        if source_snapshot and source_snapshot.get("updated_at") is not None:
            source_snapshot["updated_at"] = source_snapshot["updated_at"].isoformat()
        return {
            "name": name, "content": memory["content"],
            "entries": list(entry_rows), "source": source_snapshot,
            "updated_at": memory["updated_at"].isoformat(),
        }

    async def _journal_generation_change(self, connection: Any, name: str) -> int:
        from sqlalchemy import inspect
        names = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        if "ensemble_index_generations" not in names:
            # Memory-only stores can be used without the Ensemble Index schema.
            return 0
        from .index_store import generations
        targets = (await connection.execute(select(generations).where(
            generations.c.state.in_(("active", "building"))
        ))).mappings().all()
        if not targets:
            return 0
        snapshot = await self._snapshot_for_name(connection, name)
        now = datetime.now(timezone.utc)
        journaled = 0
        for generation in targets:
            generation_id = generation["generation_id"]
            if generation["state"] == "active":
                verification = generation["verification"] or {}
                if verification.get("acknowledged_writes_verified") is not True:
                    continue
                if snapshot is None:
                    await connection.execute(memory_generation_records.delete().where(
                        memory_generation_records.c.generation_id == generation_id,
                        memory_generation_records.c.name == name,
                    ))
                else:
                    await connection.execute(sqlite_insert(memory_generation_records).values(
                        generation_id=generation_id, name=name, snapshot=snapshot, updated_at=now,
                    ).on_conflict_do_update(
                        index_elements=[memory_generation_records.c.generation_id, memory_generation_records.c.name],
                        set_={"snapshot": snapshot, "updated_at": now},
                    ))
            else:
                await connection.execute(memory_generation_changes.insert().values(
                    generation_id=generation_id, name=name, snapshot=snapshot,
                    acknowledged=False, created_at=now,
                ))
                journaled += 1
        return journaled

    async def memory_generation_upper_bound(self) -> str | None:
        async with self.engine.connect() as connection:
            return await connection.scalar(select(func.max(memories.c.name)))

    async def list_memory_names_after(
        self, cursor: str | None, upper_bound: str | None, limit: int,
    ) -> list[str]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000")
        if upper_bound is None:
            return []
        statement = select(memories.c.name).where(memories.c.name <= upper_bound)
        if cursor is not None:
            statement = statement.where(memories.c.name > cursor)
        async with self.engine.connect() as connection:
            return list((await connection.execute(
                statement.order_by(memories.c.name).limit(limit)
            )).scalars().all())

    async def get_memory_snapshot(self, name: str) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            return await self._snapshot_for_name(connection, validate_memory_name(name))

    async def put_memory_generation_snapshot(
        self, generation_id: str, name: str, snapshot: dict[str, Any] | None,
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            if snapshot is None:
                await connection.execute(memory_generation_records.delete().where(
                    memory_generation_records.c.generation_id == generation_id,
                    memory_generation_records.c.name == name,
                ))
                return
            await connection.execute(sqlite_insert(memory_generation_records).values(
                generation_id=generation_id, name=name, snapshot=snapshot, updated_at=now,
            ).on_conflict_do_update(
                index_elements=[memory_generation_records.c.generation_id, memory_generation_records.c.name],
                set_={"snapshot": snapshot, "updated_at": now},
            ))

    async def get_memory_generation_snapshot(
        self, generation_id: str, name: str,
    ) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            return await connection.scalar(select(memory_generation_records.c.snapshot).where(
                memory_generation_records.c.generation_id == generation_id,
                memory_generation_records.c.name == validate_memory_name(name),
            ))

    async def list_memory_generation_changes_after(
        self, generation_id: str, after_change_id: int, limit: int,
    ) -> list[dict[str, Any]]:
        if isinstance(after_change_id, bool) or not isinstance(after_change_id, int) or after_change_id < 0:
            raise ValueError("after_change_id must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000")
        async with self.engine.connect() as connection:
            rows = (await connection.execute(select(memory_generation_changes).where(
                memory_generation_changes.c.generation_id == generation_id,
                memory_generation_changes.c.change_id > after_change_id,
            ).order_by(memory_generation_changes.c.change_id).limit(limit))).mappings().all()
        return [dict(row) for row in rows]

    async def memory_generation_change_high_watermark(self, generation_id: str) -> int:
        async with self.engine.connect() as connection:
            value = await connection.scalar(select(func.max(memory_generation_changes.c.change_id)).where(
                memory_generation_changes.c.generation_id == generation_id
            ))
        return int(value or 0)

    async def acknowledge_memory_generation_changes(self, generation_id: str, through_change_id: int) -> int:
        async with self.engine.begin() as connection:
            result = await connection.execute(memory_generation_changes.update().where(
                memory_generation_changes.c.generation_id == generation_id,
                memory_generation_changes.c.change_id <= through_change_id,
                memory_generation_changes.c.acknowledged.is_(False),
            ).values(acknowledged=True))
        return int(result.rowcount or 0)

    async def pending_memory_generation_changes(self, generation_id: str) -> int:
        async with self.engine.connect() as connection:
            return int(await connection.scalar(select(func.count()).select_from(
                memory_generation_changes
            ).where(
                memory_generation_changes.c.generation_id == generation_id,
                memory_generation_changes.c.acknowledged.is_(False),
            )) or 0)

    async def check_ready(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(select(1))
            for table in (
                memories, entries, memory_sources,
                memory_generation_records, memory_generation_changes,
            ):
                await connection.execute(select(table).limit(1))

    async def read(self, name: str) -> str | None:
        validate_memory_name(name)
        async with self.engine.connect() as connection:
            return await connection.scalar(select(memories.c.content).where(memories.c.name == name))

    async def delete(self, name: str) -> dict[str, Any]:
        validate_memory_name(name)
        async with self.engine.begin() as connection:
            await self._assert_generation_writes_open(connection)
            source_result = await connection.execute(
                delete(memory_sources).where(memory_sources.c.document_name == name)
            )
            entry_result = await connection.execute(
                delete(entries).where(entries.c.name == name)
            )
            memory_result = await connection.execute(
                delete(memories).where(memories.c.name == name)
            )
            await self._journal_generation_change(connection, name)
        sources_deleted = int(source_result.rowcount or 0)
        entries_deleted = int(entry_result.rowcount or 0)
        memory_deleted = int(memory_result.rowcount or 0)
        deleted = bool(sources_deleted or entries_deleted or memory_deleted)
        return {
            "status": "deleted" if deleted else "not_found",
            "memory_deleted": bool(memory_deleted),
            "entries_deleted": entries_deleted,
            "sources_deleted": sources_deleted,
        }

    async def write(self, name: str, content: str) -> None:
        validate_memory_name(name)
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if len(content) > MAX_CONTENT_CHARS:
            raise OverflowError(f"content exceeds {MAX_CONTENT_CHARS} characters")
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            await self._assert_generation_writes_open(connection)
            await connection.execute(
                sqlite_insert(memories).values(name=name, content=content, updated_at=now)
                .on_conflict_do_update(index_elements=[memories.c.name],
                                       set_={"content": content, "updated_at": now})
            )
            await self._journal_generation_change(connection, name)

    async def append(self, name: str, payload: dict[str, Any], event_id: str | None = None) -> dict[str, str]:
        validate_memory_name(name)
        if not isinstance(payload, dict) or not payload:
            raise ValueError("entry must be a non-empty JSON object")
        record = dict(payload)
        _validate_expiry(record)
        digest = None
        if event_id is not None:
            if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 256:
                raise ValueError("event_id must be a non-empty string of at most 256 characters")
            if "event_id" in record and record["event_id"] != event_id:
                raise ValueError("entry.event_id must match event_id")
            if {"payload_sha256", "captured_at"}.intersection(record):
                raise ValueError("entry contains reserved Memory metadata fields")
            record["event_id"] = event_id
            # A caller-supplied timestamp is part of ordinary event identity.
            # Transcript ingestion is the deliberate exception: its stable ID is
            # based on session/message/content, so a corrected source timestamp
            # must not duplicate or conflict with the same transcript message.
            identity_record = (
                {key: value for key, value in record.items() if key != "timestamp"}
                if name == "session-events" else record
            )
            # The generated fallback timestamp is added only after hashing.
            canonical, _ = _canonical_payload(identity_record)
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            # Match the POC ledger envelope without including service metadata
            # in the caller-payload digest.
            envelope = (
                {"schema_version": 1, "producer": "claude-code", "kind": "session_end"}
                if name == "session_events" else {}
            )
            record = {**envelope, **record}
        _canonical_payload(record)
        now = datetime.now(timezone.utc)
        record.setdefault("timestamp", now.isoformat().replace("+00:00", "Z"))
        async with self.engine.begin() as connection:
            await self._assert_generation_writes_open(connection)
            await connection.execute(
                sqlite_insert(memories).values(name=name, content="", updated_at=now)
                .on_conflict_do_nothing(index_elements=[memories.c.name])
            )
            if event_id is None:
                await connection.execute(insert(entries).values(
                    name=name, event_id=None, payload_sha256=None, payload=record, created_at=now
                ))
                await self._journal_generation_change(connection, name)
                return {"status": "stored"}
            result = await connection.execute(
                sqlite_insert(entries).values(
                    name=name, event_id=event_id, payload_sha256=digest, payload=record, created_at=now
                ).on_conflict_do_nothing(index_elements=[entries.c.name, entries.c.event_id])
            )
            if result.rowcount == 1:
                await self._journal_generation_change(connection, name)
                return {"status": "stored", "event_id": event_id}
            existing = (await connection.execute(
                select(entries.c.payload_sha256).where(entries.c.name == name, entries.c.event_id == event_id)
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
                select(entries.c.payload, entries.c.event_id, entries.c.payload_sha256, entries.c.created_at)
                .where(entries.c.name == name).order_by(entries.c.id.desc()).limit(limit)
            )).all()
        records = []
        for row in reversed(rows):
            record = dict(row.payload)
            if row.event_id is not None:
                captured_at = row.created_at
                if captured_at.tzinfo is None:
                    captured_at = captured_at.replace(tzinfo=timezone.utc)
                record.setdefault("event_id", row.event_id)
                record["payload_sha256"] = row.payload_sha256
                record["captured_at"] = captured_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            records.append(record)
        return records

    async def list_names(self) -> list[str]:
        async with self.engine.connect() as connection:
            rows = (await connection.execute(select(memories.c.name).order_by(memories.c.name))).scalars().all()
        return list(rows)

    async def _documents(self) -> tuple[list[tuple[str, str]], set[str]]:
        async with self.engine.connect() as connection:
            docs = (await connection.execute(select(memories.c.name, memories.c.content))).all()
            active = (await connection.execute(
                select(memory_sources.c.document_name).where(
                    memory_sources.c.source == "claude-code", memory_sources.c.stale.is_(False)
                )
            )).scalars().all()
        return [(name, content) for name, content in docs], set(active)

    async def search(self, keywords: list[str], limit: int = 50) -> list[dict[str, Any]]:
        if not isinstance(keywords, list) or any(not isinstance(word, str) for word in keywords):
            raise ValueError("keywords must be a list of strings")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        docs, active = await self._documents()
        return _keyword_search(docs, active, keywords, limit)

    async def search_semantic(self, query: str, top_k: int = 10, source: str | None = None) -> list[dict[str, Any]]:
        docs, active = await self._documents()
        return _semantic_search(docs, active, query, top_k, source)

    async def search_hybrid(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
            raise ValueError("top_k must be an integer between 1 and 100")
        if not isinstance(query, str) or not query.strip():
            return []
        keyword_results = await self.search(query.casefold().split(), limit=100)
        semantic_results = await self.search_semantic(query, top_k=100)
        return _fuse_memory_results(keyword_results, semantic_results, top_k)

    async def ingest_claude_markdown(self, source_path: str, content: str, sha256: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        scope, meta = _validate_ingest(source_path, content, sha256, metadata)
        document_name = "claude-code-" + hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:32]
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            await self._assert_generation_writes_open(connection)
            existing = (await connection.execute(
                select(memory_sources.c.sha256, memory_sources.c.stale).where(memory_sources.c.source_path == source_path)
            )).first()
            stored = await connection.scalar(select(memories.c.content).where(memories.c.name == document_name))
            if existing and existing.sha256 == sha256 and not existing.stale and stored is not None:
                if hashlib.sha256(stored.encode("utf-8")).hexdigest() == sha256:
                    return {"status": "unchanged", "source_path": source_path, "sha256": sha256}
            await connection.execute(
                sqlite_insert(memories).values(name=document_name, content=content, updated_at=now)
                .on_conflict_do_update(index_elements=[memories.c.name],
                                       set_={"content": content, "updated_at": now})
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
            await self._journal_generation_change(connection, document_name)
        return {"status": "ingested", "source_path": source_path, "sha256": sha256, "document": document_name}

    async def reconcile_claude_markdown(self, source: str, scope: str, paths: list[str]) -> dict[str, Any]:
        current = _validate_reconciliation(source, scope, paths)
        async with self.engine.begin() as connection:
            await self._assert_generation_writes_open(connection)
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
                names = (await connection.execute(select(memory_sources.c.document_name).where(
                    memory_sources.c.source_path.in_(stale)
                ))).scalars().all()
                for document_name in names:
                    await self._journal_generation_change(connection, document_name)
        return {"status": "reconciled", "stale": stale, "count": len(stale)}

    async def knowledge_status(self) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            count = await connection.scalar(
                select(func.count()).select_from(memory_sources.join(memories, memory_sources.c.document_name == memories.c.name))
                .where(memory_sources.c.source == "claude-code", memory_sources.c.stale.is_(False))
            )
        indexed = int(count or 0)
        return {"source": "claude-code", "status": "available" if indexed else "empty", "indexed_document_count": indexed}

    async def retrieve(self, name: str, context: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
        validate_memory_name(name)
        if not isinstance(context, dict) or not isinstance(context.get("request_id"), str) or not isinstance(context.get("objective"), str):
            raise ValueError("context requires request_id and objective")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        current_lineage = context.get("lineage", [])
        if not isinstance(current_lineage, (list, tuple)) or any(not isinstance(item, str) for item in current_lineage):
            raise ValueError("context.lineage must be a list of strings")
        records = await self.read_entries(name)
        matches = []
        for index, record in enumerate(records):
            if _entry_is_expired(record):
                continue
            prior = record.get("execution_context")
            if not isinstance(prior, dict):
                continue
            prior_lineage = prior.get("lineage", [])
            if not isinstance(prior_lineage, (list, tuple)):
                continue
            prior_id = prior.get("execution_id")
            if not prior_id or prior_id == context.get("execution_id") or prior.get("request_id") != context["request_id"]:
                continue
            if (
                len(prior_lineage) > len(current_lineage)
                and tuple(prior_lineage[:len(current_lineage)]) == tuple(current_lineage)
                and prior_lineage and prior_id == prior_lineage[-1]
            ):
                continue
            relation, rank = None, 0
            if context.get("parent_execution_id") is not None and prior_id == context["parent_execution_id"]:
                relation, rank = "parent", 100
            elif prior_lineage and len(prior_lineage) < len(current_lineage) and tuple(current_lineage[:len(prior_lineage)]) == tuple(prior_lineage) and prior_id == prior_lineage[-1]:
                relation, rank = "ancestor", 90
            elif current_lineage and prior_lineage and current_lineage[0] == prior_lineage[0]:
                relation, rank = "related-lineage", 60
            else:
                relation, rank = "same-request", 70
            matches.append({"record": record, "execution_context": prior, "relation": relation,
                            "authoritative": False, "_rank": rank, "_index": index})
        matches.sort(key=lambda item: (-item["_rank"], item["_index"]))
        for item in matches:
            item.pop("_rank", None)
            item.pop("_index", None)
        return matches[:limit]

    async def chunk(self, name: str, size: int = 2000) -> list[dict[str, Any]]:
        content = await self.read(name)
        if content is None:
            return []
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 10000:
            raise ValueError("size must be an integer between 1 and 10000")
        return _chunk_markdown(content, size)


class InMemoryMemoryStore:
    """Isolated non-durable adapter used by tests."""

    def __init__(self):
        self._memories: dict[str, str] = {}
        self._entries: dict[str, list[dict[str, Any]]] = {}
        self._entry_metadata: dict[str, list[dict[str, Any]]] = {}
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

    async def delete(self, name: str) -> dict[str, Any]:
        validate_memory_name(name)
        memory_deleted = name in self._memories
        entries_deleted = len(self._entries.pop(name, []))
        self._entry_metadata.pop(name, None)
        self._memories.pop(name, None)
        for key in [key for key in self._events if key[0] == name]:
            self._events.pop(key, None)
        source_paths = [
            path for path, source in self.sources.items()
            if source.get("document_name") == name
        ]
        for path in source_paths:
            self.sources.pop(path, None)
        deleted = memory_deleted or entries_deleted > 0 or bool(source_paths)
        return {
            "status": "deleted" if deleted else "not_found",
            "memory_deleted": memory_deleted,
            "entries_deleted": entries_deleted,
            "sources_deleted": len(source_paths),
        }

    async def append(self, name: str, payload: dict[str, Any], event_id: str | None = None) -> dict[str, str]:
        validate_memory_name(name)
        if not isinstance(payload, dict) or not payload:
            raise ValueError("entry must be a non-empty JSON object")
        record = dict(payload)
        _validate_expiry(record)
        digest = None
        if event_id is not None:
            if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 256:
                raise ValueError("event_id must be a non-empty string of at most 256 characters")
            if "event_id" in record and record["event_id"] != event_id:
                raise ValueError("entry.event_id must match event_id")
            if {"payload_sha256", "captured_at"}.intersection(record):
                raise ValueError("entry contains reserved Memory metadata fields")
            record["event_id"] = event_id
            # A caller-supplied timestamp is part of ordinary event identity.
            # Transcript ingestion is the deliberate exception: its stable ID is
            # based on session/message/content, so a corrected source timestamp
            # must not duplicate or conflict with the same transcript message.
            identity_record = (
                {key: value for key, value in record.items() if key != "timestamp"}
                if name == "session-events" else record
            )
            # The generated fallback timestamp is added only after hashing.
            canonical, _ = _canonical_payload(identity_record)
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            # Match the POC ledger envelope without including service metadata
            # in the caller-payload digest.
            envelope = (
                {"schema_version": 1, "producer": "claude-code", "kind": "session_end"}
                if name == "session_events" else {}
            )
            record = {**envelope, **record}
            existing = self._events.get((name, event_id))
            if existing is not None:
                if existing != digest:
                    raise ValueError("event_id already exists with different payload")
                return {"status": "duplicate", "event_id": event_id}
        _canonical_payload(record)
        now = datetime.now(timezone.utc)
        record.setdefault("timestamp", now.isoformat().replace("+00:00", "Z"))
        self._memories.setdefault(name, "")
        self._entries.setdefault(name, []).append(record)
        metadata = {}
        if event_id is not None:
            metadata = {"payload_sha256": digest, "captured_at": now.isoformat().replace("+00:00", "Z")}
            self._events[(name, event_id)] = digest
        self._entry_metadata.setdefault(name, []).append(metadata)
        return {"status": "stored", **({"event_id": event_id} if event_id else {})}

    async def read_entries(self, name: str, limit: int = MAX_ENTRIES) -> list[dict[str, Any]]:
        validate_memory_name(name)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_ENTRIES:
            raise ValueError(f"limit must be an integer between 1 and {MAX_ENTRIES}")
        records = self._entries.get(name, [])[-limit:]
        metadata = self._entry_metadata.get(name, [])[-limit:]
        result = []
        for record, envelope in zip(records, metadata):
            item = dict(record)
            item.update(envelope)
            result.append(item)
        return result

    async def list_names(self) -> list[str]:
        return sorted(set(self._memories) | set(self._entries))

    async def search(self, keywords: list[str], limit: int = 50) -> list[dict[str, Any]]:
        if not isinstance(keywords, list) or any(not isinstance(word, str) for word in keywords):
            raise ValueError("keywords must be a list of strings")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        active = {entry["document_name"] for entry in self.sources.values() if entry["source"] == "claude-code" and not entry["stale"]}
        return _keyword_search(list(self._memories.items()), active, keywords, limit)

    async def search_semantic(self, query: str, top_k: int = 10, source: str | None = None) -> list[dict[str, Any]]:
        active = {entry["document_name"] for entry in self.sources.values() if entry["source"] == "claude-code" and not entry["stale"]}
        return _semantic_search(list(self._memories.items()), active, query, top_k, source)

    async def search_hybrid(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
            raise ValueError("top_k must be an integer between 1 and 100")
        if not isinstance(query, str) or not query.strip():
            return []
        keyword_results = await self.search(query.casefold().split(), limit=100)
        semantic_results = await self.search_semantic(query, top_k=100)
        return _fuse_memory_results(keyword_results, semantic_results, top_k)

    async def ingest_claude_markdown(self, source_path: str, content: str, sha256: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        scope, meta = _validate_ingest(source_path, content, sha256, metadata)
        document_name = "claude-code-" + hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:32]
        existing, stored = self.sources.get(source_path), self._memories.get(document_name)
        if existing and existing["sha256"] == sha256 and not existing["stale"] and stored is not None:
            if hashlib.sha256(stored.encode("utf-8")).hexdigest() == sha256:
                return {"status": "unchanged", "source_path": source_path, "sha256": sha256}
        self._memories[document_name] = content
        self.sources[source_path] = {
            "source_path": source_path, "document_name": document_name, "source": "claude-code",
            "scope": scope, "sha256": sha256, "metadata": meta, "stale": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return {"status": "ingested", "source_path": source_path, "sha256": sha256, "document": document_name}

    async def reconcile_claude_markdown(self, source: str, scope: str, paths: list[str]) -> dict[str, Any]:
        current = _validate_reconciliation(source, scope, paths)
        stale = []
        for path, entry in self.sources.items():
            if entry["source"] == source and entry["scope"] == scope and not entry["stale"] and path not in current:
                entry["stale"] = True
                entry["updated_at"] = datetime.now(timezone.utc).isoformat()
                stale.append(path)
        stale.sort()
        return {"status": "reconciled", "stale": stale, "count": len(stale)}

    async def knowledge_status(self) -> dict[str, Any]:
        count = sum(1 for entry in self.sources.values()
                    if entry["source"] == "claude-code" and not entry["stale"] and entry["document_name"] in self._memories)
        return {"source": "claude-code", "status": "available" if count else "empty", "indexed_document_count": count}

    async def retrieve(self, name: str, context: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
        validate_memory_name(name)
        if not isinstance(context, dict) or not isinstance(context.get("request_id"), str) or not isinstance(context.get("objective"), str):
            raise ValueError("context requires request_id and objective")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        current_lineage = context.get("lineage", [])
        if not isinstance(current_lineage, (list, tuple)) or any(not isinstance(item, str) for item in current_lineage):
            raise ValueError("context.lineage must be a list of strings")
        matches = []
        for index, record in enumerate(self._entries.get(name, [])):
            if _entry_is_expired(record):
                continue
            prior = record.get("execution_context")
            if not isinstance(prior, dict) or prior.get("request_id") != context["request_id"]:
                continue
            prior_lineage = prior.get("lineage", [])
            if not isinstance(prior_lineage, (list, tuple)):
                continue
            prior_id = prior.get("execution_id")
            if not prior_id or prior_id == context.get("execution_id"):
                continue
            if len(prior_lineage) > len(current_lineage) and tuple(prior_lineage[:len(current_lineage)]) == tuple(current_lineage) and prior_id == prior_lineage[-1]:
                continue
            if context.get("parent_execution_id") == prior_id:
                relation, rank = "parent", 100
            elif prior_lineage and len(prior_lineage) < len(current_lineage) and tuple(current_lineage[:len(prior_lineage)]) == tuple(prior_lineage) and prior_id == prior_lineage[-1]:
                relation, rank = "ancestor", 90
            elif prior_lineage and current_lineage and prior_lineage[0] == current_lineage[0]:
                relation, rank = "related-lineage", 60
            else:
                relation, rank = "same-request", 70
            matches.append({"record": record, "execution_context": prior, "relation": relation,
                            "authoritative": False, "_rank": rank, "_index": index})
        matches.sort(key=lambda item: (-item["_rank"], item["_index"]))
        for item in matches:
            item.pop("_rank", None)
            item.pop("_index", None)
        return matches[:limit]

    async def chunk(self, name: str, size: int = 2000) -> list[dict[str, Any]]:
        content = await self.read(name)
        if content is None:
            return []
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 10000:
            raise ValueError("size must be an integer between 1 and 10000")
        return _chunk_markdown(content, size)
