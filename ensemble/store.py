from __future__ import annotations

import os
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON, MetaData,
    String, Table, Text, UniqueConstraint, delete, func, insert, inspect, select, text, update,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.engine import make_url

from .partitioning import PartitionedAsyncEngine

from .tokenization import tokenize

from .models import Chunk, Document


metadata = MetaData()
documents = Table(
    "ensemble_documents",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("source_id", String(1024), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("title", String(2048), nullable=False),
    Column("content", Text, nullable=False),
    Column("metadata", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("is_current", Boolean, nullable=False, default=True),
    UniqueConstraint("source_id", "revision", name="uq_ensemble_document_source_revision"),
)
chunks = Table(
    "ensemble_chunks",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("document_id", String(36), ForeignKey("ensemble_documents.id", ondelete="CASCADE"), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("content", Text, nullable=False),
    Column("embedding", JSON, nullable=False),
    Column("profile", String(64), nullable=False, default="window-v1"),
    Column("start_offset", Integer, nullable=False, default=0),
    Column("end_offset", Integer, nullable=False, default=0),
    Column("header", Text, nullable=False, default=""),
    UniqueConstraint("document_id", "ordinal", name="uq_ensemble_chunk_document_ordinal"),
)
Index("ix_ensemble_documents_current_source", documents.c.source_id, documents.c.is_current)
Index("ix_ensemble_chunks_document", chunks.c.document_id)

generation_documents = Table(
    "ensemble_document_generation_records",
    metadata,
    Column("generation_id", String(64), primary_key=True),
    Column("document_id", String(36), primary_key=True),
    Column("source_id", String(1024), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("title", String(2048), nullable=False),
    Column("content", Text, nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("is_current", Boolean, nullable=False),
    UniqueConstraint("generation_id", "source_id", "revision", name="uq_generation_document_source_revision"),
)
generation_chunks = Table(
    "ensemble_chunk_generation_records",
    metadata,
    Column("generation_id", String(64), primary_key=True),
    Column("chunk_id", String(36), primary_key=True),
    Column("document_id", String(36), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("content", Text, nullable=False),
    Column("embedding", JSON, nullable=False),
    Column("profile", String(64), nullable=False),
    Column("start_offset", Integer, nullable=False),
    Column("end_offset", Integer, nullable=False),
    Column("header", Text, nullable=False),
    ForeignKeyConstraint(
        ["generation_id", "document_id"],
        ["ensemble_document_generation_records.generation_id",
         "ensemble_document_generation_records.document_id"],
        ondelete="CASCADE",
    ),
    UniqueConstraint("generation_id", "document_id", "ordinal", name="uq_generation_chunk_document_ordinal"),
)
Index("ix_generation_documents_current_source", generation_documents.c.generation_id, generation_documents.c.source_id, generation_documents.c.is_current)
Index("ix_generation_chunks_document", generation_chunks.c.generation_id, generation_chunks.c.document_id)

generation_source_tombstones = Table(
    "ensemble_generation_source_tombstones",
    metadata,
    Column("generation_id", String(64), primary_key=True),
    Column("source_id", String(1024), primary_key=True),
    Column("tombstoned_at", DateTime(timezone=True), nullable=False),
)


@dataclass(frozen=True)
class IndexedChunk:
    document: Document
    chunk: Chunk


class DocumentStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def check_ready(self) -> None: ...
    async def save(self, document: Document, chunks: list[Chunk]) -> Document: ...
    async def tombstone_source(self, source_id: str) -> None: ...
    async def restore_source(self, source_id: str) -> None: ...
    async def reclaim_source(self, source_id: str) -> int: ...
    async def all_chunks(self) -> list[IndexedChunk]: ...
    async def lexical_search(self, query: str, limit: int = 200) -> list[tuple[IndexedChunk, float]]: ...
    async def upsert_generation(self, generation_id: str, document: Document, chunks: list[Chunk]) -> Document: ...
    async def generation_all_chunks(self, generation_id: str) -> list[IndexedChunk]: ...
    async def generation_lexical_search(self, generation_id: str, query: str, limit: int = 200) -> list[tuple[IndexedChunk, float]]: ...
    async def tombstone_generation_source(self, generation_id: str, source_id: str) -> int: ...
    async def reclaim_generation_source(self, generation_id: str, source_id: str) -> int: ...
    async def delete_generation(self, generation_id: str) -> int: ...


class InMemoryDocumentStore:
    """Non-durable adapter retained for isolated tests and development."""

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def check_ready(self) -> None:
        return None

    async def save(self, document: Document, chunks: list[Chunk]) -> Document:
        if document.source_id in self._tombstoned:
            raise ValueError("source is tombstoned")
        key = (document.source_id, document.revision)
        previous = self._documents.get(key)
        if previous is not None:
            document.id = previous.id
            document.created_at = previous.created_at
            self._chunks.pop(previous.id, None)
        self._documents[key] = document
        self._chunks[document.id] = list(chunks)
        return document

    async def tombstone_source(self, source_id: str) -> None:
        self._tombstoned.add(source_id)

    async def restore_source(self, source_id: str) -> None:
        self._tombstoned.discard(source_id)

    async def reclaim_source(self, source_id: str) -> int:
        keys = [key for key in self._documents if key[0] == source_id]
        selected = keys[:500]
        document_ids = {self._documents[key].id for key in selected}
        for key in selected:
            del self._documents[key]
        for document_id in document_ids:
            self._chunks.pop(document_id, None)
        return 501 if len(keys) > 500 else len(selected)

    async def all_chunks(self) -> list[IndexedChunk]:
        latest: dict[str, Document] = {}
        for document in self._documents.values():
            if document.source_id in self._tombstoned:
                continue
            current = latest.get(document.source_id)
            if current is None or document.revision > current.revision:
                latest[document.source_id] = document
        result: list[IndexedChunk] = []
        for document in latest.values():
            result.extend(
                IndexedChunk(document, chunk)
                for chunk in self._chunks.get(document.id, [])
            )
        return result

    async def lexical_search(self, query: str, limit: int = 200) -> list[tuple[IndexedChunk, float]]:
        terms = {item["term"] for item in tokenize("lexical-v1", query)}
        if not terms:
            return []
        matches = []
        for item in await self.all_chunks():
            content_terms = {token["term"] for token in tokenize("lexical-v1", item.chunk.content)}
            overlap = len(terms & content_terms)
            if overlap:
                matches.append((item, overlap / len(terms)))
        return sorted(matches, key=lambda pair: (-pair[1], str(pair[0].chunk.id)))[:limit]


    def __init__(self):
        self._documents: dict[tuple[str, int], Document] = {}
        self._chunks: dict[UUID, list[Chunk]] = {}
        self._tombstoned: set[str] = set()
        self._generation_data: dict[str, dict[tuple[str, int], tuple[Document, list[Chunk]]]] = {}
        self._generation_tombstones: dict[str, set[str]] = {}

    async def upsert_generation(self, generation_id: str, document: Document, chunks: list[Chunk]) -> Document:
        if not generation_id or len(generation_id) > 64:
            raise ValueError("generation_id must contain 1-64 characters")
        if document.source_id in self._generation_tombstones.get(generation_id, set()):
            raise ValueError("source is tombstoned in this generation")
        generation = self._generation_data.setdefault(generation_id, {})
        key = (document.source_id, document.revision)
        previous = generation.get(key)
        if previous is not None:
            document.id = previous[0].id
            document.created_at = previous[0].created_at
        generation[key] = (document, list(chunks))
        latest = max(revision for source, revision in generation if source == document.source_id)
        for (source, revision), (stored, _) in generation.items():
            if source == document.source_id:
                stored.metadata = dict(stored.metadata)
                stored.metadata["_generation_current"] = revision == latest
        return document

    async def generation_all_chunks(self, generation_id: str) -> list[IndexedChunk]:
        generation = self._generation_data.get(generation_id, {})
        latest: dict[str, tuple[Document, list[Chunk]]] = {}
        tombstones = self._generation_tombstones.get(generation_id, set())
        for (source_id, _revision), pair in generation.items():
            if source_id in tombstones:
                continue
            current = latest.get(source_id)
            if current is None or pair[0].revision > current[0].revision:
                latest[source_id] = pair
        return [
            IndexedChunk(document, chunk)
            for document, chunks_for_doc in latest.values()
            for chunk in chunks_for_doc
        ]

    async def generation_lexical_search(self, generation_id: str, query: str, limit: int = 200) -> list[tuple[IndexedChunk, float]]:
        terms = {item["term"] for item in tokenize("lexical-v1", query)}
        if not terms:
            return []
        matches = []
        for item in await self.generation_all_chunks(generation_id):
            content_terms = {token["term"] for token in tokenize("lexical-v1", item.chunk.content)}
            overlap = len(terms & content_terms)
            if overlap:
                matches.append((item, overlap / len(terms)))
        return sorted(matches, key=lambda pair: (-pair[1], str(pair[0].chunk.id)))[:limit]

    async def tombstone_generation_source(self, generation_id: str, source_id: str) -> int:
        if not generation_id or not source_id:
            raise ValueError("generation_id and source_id are required")
        tombstones = self._generation_tombstones.setdefault(generation_id, set())
        if source_id in tombstones:
            return 0
        tombstones.add(source_id)
        generation = self._generation_data.get(generation_id, {})
        for (stored_source, _revision), (document, _chunks) in generation.items():
            if stored_source == source_id:
                document.metadata = dict(document.metadata)
                document.metadata["_generation_tombstoned"] = True
        return 1

    async def delete_generation(self, generation_id: str) -> int:
        self._generation_tombstones.pop(generation_id, None)
        return len(self._generation_data.pop(generation_id, {}))


class SqlDocumentStore:
    """Durable SQLite store. Alternative persistence implementations belong to FlossWare proper."""

    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.getenv(
            "ENSEMBLE_DATABASE_URL",
            "sqlite+aiosqlite:///./.ensemble/ensemble.db",
        )
        url = make_url(self.database_url)
        if url.get_backend_name() != "sqlite":
            raise ValueError("Ensemble supports SQLite persistence only; alternative persistence implementations belong to FlossWare proper.")
        if url.database not in (None, "", ":memory:"):
            Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

        self.engine: AsyncEngine = PartitionedAsyncEngine(
            self.database_url,
            pool_pre_ping=True,
        )
        self.metadata = metadata
        self.documents = documents
        self.chunks = chunks

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            tables = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_table_names()
            )
        if "alembic_version" not in tables:
            raise RuntimeError(
                "Ensemble database schema is not initialized; run 'alembic upgrade head'"
            )
        missing = {"ensemble_documents", "ensemble_chunks", "ensemble_vector_collections", "ensemble_vector_records", "ensemble_index_sources", "ensemble_knowledge_records", "ensemble_graph_nodes", "ensemble_graph_edges", "ensemble_memories", "ensemble_memory_entries", "ensemble_memory_sources", "ensemble_ga_runs", "ensemble_ga_promotions", "ensemble_thompson_models", "ensemble_thompson_events", "ensemble_learning_outcomes", "ensemble_learning_artifacts", "ensemble_lexical_fts", "ensemble_transcript_checkpoints", "ensemble_transcript_jobs", "ensemble_partition_metadata", "ensemble_index_generations", "ensemble_index_generation_control", "ensemble_vector_generation_records", "ensemble_document_generation_records", "ensemble_chunk_generation_records", "ensemble_generation_source_tombstones", "ensemble_index_generation_sources", "ensemble_source_reclamation", "ensemble_index_generation_changes", "ensemble_index_generation_write_fence", "ensemble_knowledge_generation_records"} - set(tables)
        if missing:
            raise RuntimeError(
                "Ensemble database schema is incomplete; run 'alembic upgrade head'"
            )
        async with self.engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != "0029_crawl_jobs":
            raise RuntimeError(
                "Ensemble database schema revision is not supported; run 'alembic upgrade head'"
            )

    async def close(self) -> None:
        await self.engine.dispose()

    async def check_ready(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(select(1))

    async def save(self, document: Document, chunks: list[Chunk]) -> Document:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

        async with self.engine.begin() as connection:
            from .index_store import assert_generation_writes_open
            await assert_generation_writes_open(connection)
            # The unique source/revision constraint plus ON CONFLICT makes retries
            # and concurrent submissions converge on one stable document identity.
            statement = dialect_insert(self.documents).values(
                id=str(document.id),
                source_id=document.source_id,
                revision=document.revision,
                title=document.title,
                content=document.content,
                metadata=document.metadata,
                created_at=document.created_at,
                is_current=False,
            ).on_conflict_do_nothing(
                index_elements=["source_id", "revision"]
            )
            await connection.execute(statement)

            existing = (
                await connection.execute(
                    select(self.documents).where(
                        self.documents.c.source_id == document.source_id,
                        self.documents.c.revision == document.revision,
                    )
                )
            ).mappings().one()
            document.id = UUID(existing["id"])
            document.created_at = existing["created_at"]

            await connection.execute(
                update(self.documents)
                .where(self.documents.c.id == str(document.id))
                .values(title=document.title, content=document.content, metadata=document.metadata, is_current=False)
            )
            await connection.execute(
                update(self.documents)
                .where(self.documents.c.source_id == document.source_id)
                .values(is_current=False)
            )

            latest_revision = await connection.scalar(
                select(func.max(self.documents.c.revision)).where(
                    self.documents.c.source_id == document.source_id
                )
            )
            await connection.execute(
                update(self.documents)
                .where(
                    self.documents.c.source_id == document.source_id,
                    self.documents.c.revision == latest_revision,
                )
                .values(is_current=True)
            )

            await connection.execute(
                text("DELETE FROM ensemble_lexical_fts WHERE document_id = :document_id"),
                {"document_id": str(document.id)},
            )
            await connection.execute(
                delete(self.chunks).where(self.chunks.c.document_id == str(document.id))
            )
            if chunks:
                await connection.execute(insert(self.chunks), [
                    {
                        "id": str(chunk.id),
                        "document_id": str(document.id),
                        "ordinal": chunk.ordinal,
                        "content": chunk.content,
                        "embedding": chunk.embedding,
                        "profile": chunk.profile,
                        "start_offset": chunk.start,
                        "end_offset": chunk.end,
                        "header": chunk.header,
                    }
                    for chunk in chunks
                ])
                lexical_rows = []
                for chunk in chunks:
                    analyzed = " ".join(token["term"] for token in tokenize("lexical-v1", chunk.content))
                    lexical_rows.append({
                        "chunk_id": str(chunk.id),
                        "document_id": str(document.id),
                        "source_id": document.source_id,
                        "title": document.title,
                        "analyzed": analyzed,
                    })
                await connection.execute(
                    text(
                        "INSERT INTO ensemble_lexical_fts "
                        "(chunk_id, document_id, source_id, title, analyzed) "
                        "VALUES (:chunk_id, :document_id, :source_id, :title, :analyzed)"
                    ),
                    lexical_rows,
                )
        return document

    async def tombstone_source(self, source_id: str) -> None:
        async with self.engine.begin() as connection:
            from .index_store import assert_generation_writes_open
            await assert_generation_writes_open(connection)
            await connection.execute(
                update(self.documents).where(self.documents.c.source_id == source_id)
                .values(is_current=False)
            )

    async def restore_source(self, source_id: str) -> None:
        async with self.engine.begin() as connection:
            from .index_store import assert_generation_writes_open
            await assert_generation_writes_open(connection)
            latest = (await connection.execute(
                select(self.documents.c.id).where(self.documents.c.source_id == source_id)
                .order_by(self.documents.c.revision.desc()).limit(1)
            )).scalar_one_or_none()
            await connection.execute(
                update(self.documents).where(self.documents.c.source_id == source_id)
                .values(is_current=False)
            )
            if latest is not None:
                await connection.execute(
                    update(self.documents).where(self.documents.c.id == latest)
                    .values(is_current=True)
                )

    async def reclaim_source(self, source_id: str) -> int:
        async with self.engine.begin() as connection:
            from .index_store import assert_generation_writes_open
            await assert_generation_writes_open(connection)
            await connection.execute(
                text("DELETE FROM ensemble_lexical_fts WHERE source_id = :source_id"),
                {"source_id": source_id},
            )
            document_ids = (await connection.execute(
                select(self.documents.c.id).where(self.documents.c.source_id == source_id)
                .order_by(self.documents.c.id).limit(501)
            )).scalars().all()
            more = len(document_ids) > 500
            document_ids = document_ids[:500]
            if document_ids:
                await connection.execute(delete(self.chunks).where(self.chunks.c.document_id.in_(document_ids)))
                await connection.execute(delete(self.documents).where(self.documents.c.id.in_(document_ids)))
        return 501 if more else len(document_ids)

    async def all_chunks(self) -> list[IndexedChunk]:
        statement = (
            select(
                self.documents.c.id.label("document_id"),
                self.documents.c.source_id,
                self.documents.c.revision,
                self.documents.c.title,
                self.documents.c.content.label("document_content"),
                self.documents.c.metadata,
                self.documents.c.created_at,
                self.chunks.c.id.label("chunk_id"),
                self.chunks.c.ordinal,
                self.chunks.c.content.label("chunk_content"),
                self.chunks.c.embedding,
                self.chunks.c.profile,
                self.chunks.c.start_offset,
                self.chunks.c.end_offset,
                self.chunks.c.header,
            )
            .join(self.chunks, self.documents.c.id == self.chunks.c.document_id)
            .where(self.documents.c.is_current.is_(True))
            .order_by(self.documents.c.source_id, self.chunks.c.ordinal)
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()

        result: list[IndexedChunk] = []
        for row in rows:
            document = Document(
                row["source_id"], row["revision"], row["title"], row["document_content"], row["metadata"]
            )
            document.id = UUID(row["document_id"])
            document.created_at = row["created_at"]
            chunk = Chunk(
                document.id, row["ordinal"], row["chunk_content"], row["embedding"],
                profile=row["profile"], start=row["start_offset"],
                end=row["end_offset"], header=row["header"],
            )
            chunk.id = UUID(row["chunk_id"])
            result.append(IndexedChunk(document, chunk))
        return result


    async def upsert_generation(self, generation_id: str, document: Document, chunks: list[Chunk]) -> Document:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

        if not generation_id or len(generation_id) > 64:
            raise ValueError("generation_id must contain 1-64 characters")
        async with self.engine.begin() as connection:
            from .index_store import assert_generation_writes_open
            await assert_generation_writes_open(connection)
            exists = await connection.scalar(text(
                "SELECT COUNT(*) FROM ensemble_index_generations WHERE generation_id = :generation_id"
            ), {"generation_id": generation_id})
            if not exists:
                raise ValueError("unknown index generation")
            tombstoned = await connection.scalar(
                select(func.count()).select_from(generation_source_tombstones).where(
                    generation_source_tombstones.c.generation_id == generation_id,
                    generation_source_tombstones.c.source_id == document.source_id,
                )
            )
            if tombstoned:
                raise ValueError("source is tombstoned in this generation")
            statement = dialect_insert(generation_documents).values(
                generation_id=generation_id, document_id=str(document.id),
                source_id=document.source_id, revision=document.revision,
                title=document.title, content=document.content,
                metadata=document.metadata, created_at=document.created_at,
                is_current=False,
            ).on_conflict_do_nothing(
                index_elements=["generation_id", "source_id", "revision"]
            )
            await connection.execute(statement)
            existing = (await connection.execute(
                select(generation_documents).where(
                    generation_documents.c.generation_id == generation_id,
                    generation_documents.c.source_id == document.source_id,
                    generation_documents.c.revision == document.revision,
                )
            )).mappings().one()
            document.id = UUID(existing["document_id"])
            document.created_at = existing["created_at"]
            await connection.execute(
                generation_documents.update().where(
                    generation_documents.c.generation_id == generation_id,
                    generation_documents.c.document_id == str(document.id),
                ).values(
                    title=document.title, content=document.content,
                    metadata=document.metadata, is_current=False,
                )
            )
            latest_revision = await connection.scalar(
                select(func.max(generation_documents.c.revision)).where(
                    generation_documents.c.generation_id == generation_id,
                    generation_documents.c.source_id == document.source_id,
                )
            )
            await connection.execute(
                generation_documents.update().where(
                    generation_documents.c.generation_id == generation_id,
                    generation_documents.c.source_id == document.source_id,
                ).values(is_current=False)
            )
            await connection.execute(
                generation_documents.update().where(
                    generation_documents.c.generation_id == generation_id,
                    generation_documents.c.source_id == document.source_id,
                    generation_documents.c.revision == latest_revision,
                ).values(is_current=True)
            )
            await connection.execute(delete(generation_chunks).where(
                generation_chunks.c.generation_id == generation_id,
                generation_chunks.c.document_id == str(document.id),
            ))
            if chunks:
                await connection.execute(insert(generation_chunks), [{
                    "generation_id": generation_id, "chunk_id": str(chunk.id),
                    "document_id": str(document.id), "ordinal": chunk.ordinal,
                    "content": chunk.content, "embedding": chunk.embedding,
                    "profile": chunk.profile, "start_offset": chunk.start,
                    "end_offset": chunk.end, "header": chunk.header,
                } for chunk in chunks])
        return document

    async def generation_all_chunks(self, generation_id: str) -> list[IndexedChunk]:
        statement = (
            select(
                generation_documents.c.document_id, generation_documents.c.source_id,
                generation_documents.c.revision, generation_documents.c.title,
                generation_documents.c.content.label("document_content"),
                generation_documents.c.metadata, generation_documents.c.created_at,
                generation_chunks.c.chunk_id, generation_chunks.c.ordinal,
                generation_chunks.c.content.label("chunk_content"),
                generation_chunks.c.embedding, generation_chunks.c.profile,
                generation_chunks.c.start_offset, generation_chunks.c.end_offset,
                generation_chunks.c.header,
            )
            .join(
                generation_chunks,
                (generation_documents.c.generation_id == generation_chunks.c.generation_id)
                & (generation_documents.c.document_id == generation_chunks.c.document_id),
            )
            .where(
                generation_documents.c.generation_id == generation_id,
                generation_documents.c.is_current.is_(True),
            )
            .order_by(generation_documents.c.source_id, generation_chunks.c.ordinal)
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        result = []
        for row in rows:
            document = Document(
                row["source_id"], row["revision"], row["title"],
                row["document_content"], row["metadata"],
            )
            document.id = UUID(row["document_id"])
            document.created_at = row["created_at"]
            chunk = Chunk(
                document.id, row["ordinal"], row["chunk_content"], row["embedding"],
                profile=row["profile"], start=row["start_offset"],
                end=row["end_offset"], header=row["header"],
            )
            chunk.id = UUID(row["chunk_id"])
            result.append(IndexedChunk(document, chunk))
        return result

    async def generation_lexical_search(self, generation_id: str, query: str, limit: int = 200) -> list[tuple[IndexedChunk, float]]:
        terms = {item["term"] for item in tokenize("lexical-v1", query)}
        if not terms:
            return []
        matches = []
        for item in await self.generation_all_chunks(generation_id):
            content_terms = {token["term"] for token in tokenize("lexical-v1", item.chunk.content)}
            overlap = len(terms & content_terms)
            if overlap:
                matches.append((item, overlap / len(terms)))
        return sorted(matches, key=lambda pair: (-pair[1], str(pair[0].chunk.id)))[:limit]

    async def tombstone_generation_source(self, generation_id: str, source_id: str) -> int:
        if not generation_id or not source_id:
            raise ValueError("generation_id and source_id are required")
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
        async with self.engine.begin() as connection:
            inserted = await connection.execute(
                dialect_insert(generation_source_tombstones).values(
                    generation_id=generation_id, source_id=source_id,
                    tombstoned_at=datetime.now(timezone.utc),
                ).on_conflict_do_nothing(
                    index_elements=["generation_id", "source_id"]
                )
            )
            await connection.execute(
                generation_documents.update().where(
                    generation_documents.c.generation_id == generation_id,
                    generation_documents.c.source_id == source_id,
                ).values(is_current=False)
            )
        return inserted.rowcount or 0

    async def reclaim_generation_source(self, generation_id: str, source_id: str) -> int:
        """Remove one bounded page of a purged source's generation documents and chunks."""
        async with self.engine.begin() as connection:
            document_ids = (await connection.execute(
                select(generation_documents.c.document_id).where(
                    generation_documents.c.generation_id == generation_id,
                    generation_documents.c.source_id == source_id,
                ).order_by(generation_documents.c.document_id).limit(501)
            )).scalars().all()
            more = len(document_ids) > 500
            document_ids = document_ids[:500]
            if document_ids:
                await connection.execute(delete(generation_chunks).where(
                    generation_chunks.c.generation_id == generation_id,
                    generation_chunks.c.document_id.in_(document_ids),
                ))
                await connection.execute(delete(generation_documents).where(
                    generation_documents.c.generation_id == generation_id,
                    generation_documents.c.document_id.in_(document_ids),
                ))
        return 501 if more else len(document_ids)

    async def delete_generation(self, generation_id: str) -> int:
        async with self.engine.begin() as connection:
            count = await connection.scalar(
                select(func.count()).select_from(generation_documents).where(
                    generation_documents.c.generation_id == generation_id
                )
            )
            await connection.execute(delete(generation_chunks).where(
                generation_chunks.c.generation_id == generation_id
            ))
            await connection.execute(delete(generation_documents).where(
                generation_documents.c.generation_id == generation_id
            ))
            await connection.execute(delete(generation_source_tombstones).where(
                generation_source_tombstones.c.generation_id == generation_id
            ))
        return int(count or 0)

    async def lexical_search(self, query: str, limit: int = 200) -> list[tuple[IndexedChunk, float]]:
        terms = []
        seen = set()
        for token in tokenize("lexical-v1", query):
            term = token["term"]
            if term not in seen:
                seen.add(term)
                terms.append('"' + term.replace('"', '""') + '"')
        if not terms:
            return []
        expression = " OR ".join(terms)
        statement = text("""
            SELECT chunk_id, bm25(ensemble_lexical_fts) AS score
            FROM ensemble_lexical_fts
            WHERE analyzed MATCH :expression
            ORDER BY score
            LIMIT :limit
        """)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                statement, {"expression": expression, "limit": limit}
            )).mappings().all()
        if not rows:
            return []
        by_id = {str(item.chunk.id): item for item in await self.all_chunks()}
        return [
            (by_id[row["chunk_id"]], -float(row["score"]))
            for row in rows if row["chunk_id"] in by_id
        ]

