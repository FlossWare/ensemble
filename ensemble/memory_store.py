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
    Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, MetaData, String, Table,
    Text, UniqueConstraint, delete, func, insert, select, update,
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
    Column("stale", Boolean, nullable=False, default=False),
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




def _vectorize_text(text: str) -> dict[str, float]:
    terms = []
    for token in re.findall(r"[^\\W_]+(?:[._:-][^\\W_]+)*", text.casefold(), re.UNICODE):
        if len(token) > 2:
            terms.append(token)
        terms.extend(part for part in re.split(r"[._:-]+", token) if len(part) > 2)
    counts = {}
    for term in terms:
        counts[term] = counts.get(term, 0) + 1
    length = max(1, len(terms))
    return {term: count / length for term, count in counts.items()}


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    terms = set(left) | set(right)
    dot = sum(left.get(term, 0.0) * right.get(term, 0.0) for term in terms)
    norm_left = sum(value * value for value in left.values()) ** 0.5
    norm_right = sum(value * value for value in right.values()) ** 0.5
    return dot / (norm_left * norm_right) if norm_left and norm_right else 0.0


def _chunk_markdown(content: str, max_chunk_size: int = 2000) -> list[dict[str, Any]]:
    lines = content.splitlines(keepends=True)
    chunks = []
    current: list[str] = []
    header = "intro"
    start_line = 0
    size = 0
    for line_number, line in enumerate(lines):
        if line.startswith("#"):
            if current:
                text = "".join(current)
                chunks.append({"header": header, "start_line": start_line, "end_line": line_number,
                               "content": text, "size": len(text)})
            header = line.lstrip("#").strip()
            current = [line]
            start_line = line_number
            size = len(line)
            continue
        current.append(line)
        size += len(line)
        if size > max_chunk_size:
            text = "".join(current)
            chunks.append({"header": header, "start_line": start_line, "end_line": line_number + 1,
                           "content": text, "size": len(text)})
            current, size, start_line = [], 0, line_number + 1
    if current:
        text = "".join(current)
        chunks.append({"header": header, "start_line": start_line, "end_line": len(lines),
                       "content": text, "size": len(text)})
    return chunks


def _keyword_search(documents: list[tuple[str, str]], active_knowledge: set[str], keywords: list[str], limit: int) -> list[dict[str, Any]]:
    keyword_set = {word.casefold() for word in keywords if isinstance(word, str) and word.strip()}
    if not keyword_set:
        return []
    terms_by_document = []
    for name, content in documents:
        if name.startswith("claude-code-") and name not in active_knowledge:
            continue
        chunks = _chunk_markdown(content) or [{"header": "full", "content": content}]
        for chunk in chunks:
            terms = [word.strip(".,!?;:").casefold() for word in chunk["content"].split() if len(word) > 2]
            terms_by_document.append((name, chunk, terms, set(terms)))
    doc_count = len(terms_by_document)
    idf = {
        term: math.log(doc_count / max(1, sum(1 for _, _, _, terms in terms_by_document if term in terms)))
        for term in keyword_set
    }
    results = []
    for name, chunk, terms, term_set in terms_by_document:
        counts = {}
        for term in terms:
            counts[term] = counts.get(term, 0) + 1
        score, matched = 0.0, []
        for term in keyword_set:
            if term in counts:
                score += (counts[term] / max(1, len(terms))) * idf[term]
                matched.append(term)
        if score > 0:
            results.append({
                "file": name, "section": chunk["header"], "score": score,
                "matched_keywords": len(matched), "size_bytes": len(chunk["content"].encode("utf-8")),
            })
    return sorted(results, key=lambda item: (-item["score"], item["file"], item["section"]))[:limit]


def _semantic_search(
    documents: list[tuple[str, str]], active_knowledge: set[str], query: str, top_k: int,
    source: str | None,
) -> list[dict[str, Any]]:
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
            or (bool(re.search(r"[-._]", normalized)) and bool(re.search(r"\\d", normalized)))
            or normalized.count(".") >= 2
        )
    )
    pattern = re.compile(rf"(?<![\\w.-]){re.escape(normalized)}(?![\\w.-])", re.UNICODE)
    results = []
    for name, content in documents:
        is_knowledge = name in active_knowledge
        if name.startswith("claude-code-") and not is_knowledge:
            continue
        if source == "claude-code" and not is_knowledge:
            continue
        if source == "memory" and is_knowledge:
            continue
        for chunk in _chunk_markdown(content) or [{"header": "full", "content": content}]:
            exact = bool(pattern.search(chunk["content"].casefold())) if identifier else normalized in chunk["content"].casefold()
            if identifier and not exact:
                continue
            score = 1.0 if exact else _cosine(query_vector, _vectorize_text(chunk["content"]))
            if exact or score > 0.1:
                result = {
                    "file": name, "section": chunk["header"], "score": score,
                    "size_bytes": len(chunk["content"].encode("utf-8")), "content": content,
                }
                if is_knowledge:
                    result["source"] = "claude-code"
                results.append(result)
    return sorted(results, key=lambda item: (-item["score"], item["file"], item["section"]))[:top_k]


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
    async def search(self, keywords: list[str], limit: int = 50) -> list[dict[str, Any]]:
        if not isinstance(keywords, list) or any(not isinstance(word, str) for word in keywords):
            raise ValueError("keywords must be a list of strings")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        async with self.engine.connect() as connection:
            documents = (await connection.execute(select(memories.c.name, memories.c.content))).all()
            active = (await connection.execute(
                select(memory_sources.c.document_name).where(
                    memory_sources.c.source == "claude-code", memory_sources.c.stale.is_(False)
                )
            )).scalars().all()
        return _keyword_search([(name, content) for name, content in documents], set(active), keywords, limit)

    async def search_semantic(self, query: str, top_k: int = 10, source: str | None = None) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            documents = (await connection.execute(select(memories.c.name, memories.c.content))).all()
            active = (await connection.execute(
                select(memory_sources.c.document_name).where(
                    memory_sources.c.source == "claude-code", memory_sources.c.stale.is_(False)
                )
            )).scalars().all()
        return _semantic_search([(name, content) for name, content in documents], set(active), query, top_k, source)

    async def search_hybrid(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
            raise ValueError("top_k must be an integer between 1 and 100")
        keywords = query.casefold().split() if isinstance(query, str) else []
        keyword_results = await self.search(keywords, limit=100)
        semantic_results = await self.search_semantic(query, top_k=top_k)
        merged: dict[tuple[str, str], dict[str, float]] = {}
        for result in keyword_results:
            key = (result["file"], result.get("section", "full"))
            merged.setdefault(key, {"keyword_score": 0.0, "semantic_score": 0.0})
            merged[key]["keyword_score"] = result["score"]
        for result in semantic_results:
            key = (result["file"], result.get("section", "full"))
            merged.setdefault(key, {"keyword_score": 0.0, "semantic_score": 0.0})
            merged[key]["semantic_score"] = result["score"]
        results = [{
            "file": name, "section": section,
            "score": 0.4 * scores["keyword_score"] + 0.6 * scores["semantic_score"],
            "keyword_score": scores["keyword_score"], "semantic_score": scores["semantic_score"],
        } for (name, section), scores in merged.items()]
        return sorted(results, key=lambda item: (-item["score"], item["file"], item["section"]))[:top_k]

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
        docs = list(self._memories.items())
        active = {entry["document_name"] for entry in self.sources.values() if entry["source"] == "claude-code" and not entry["stale"]}
        return _keyword_search(docs, active, keywords, limit)

    async def search_semantic(self, query: str, top_k: int = 10, source: str | None = None) -> list[dict[str, Any]]:
        active = {entry["document_name"] for entry in self.sources.values() if entry["source"] == "claude-code" and not entry["stale"]}
        return _semantic_search(list(self._memories.items()), active, query, top_k, source)

    async def search_hybrid(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
            raise ValueError("top_k must be an integer between 1 and 100")
        keyword_results = await self.search(query.casefold().split() if isinstance(query, str) else [], limit=100)
        semantic_results = await self.search_semantic(query, top_k=top_k)
        merged: dict[tuple[str, str], dict[str, float]] = {}
        for result in keyword_results:
            key = (result["file"], result.get("section", "full"))
            merged.setdefault(key, {"keyword_score": 0.0, "semantic_score": 0.0})
            merged[key]["keyword_score"] = result["score"]
        for result in semantic_results:
            key = (result["file"], result.get("section", "full"))
            merged.setdefault(key, {"keyword_score": 0.0, "semantic_score": 0.0})
            merged[key]["semantic_score"] = result["score"]
        results = [{
            "file": name, "section": section,
            "score": 0.4 * scores["keyword_score"] + 0.6 * scores["semantic_score"],
            "keyword_score": scores["keyword_score"], "semantic_score": scores["semantic_score"],
        } for (name, section), scores in merged.items()]
        return sorted(results, key=lambda item: (-item["score"], item["file"], item["section"]))[:top_k]

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
