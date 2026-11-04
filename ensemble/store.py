from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, MetaData,
    String, Table, Text, UniqueConstraint, delete, func, insert, inspect, select, text, update,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.engine import make_url

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
    async def all_chunks(self) -> list[IndexedChunk]: ...
    async def lexical_search(self, query: str, limit: int = 200) -> list[tuple[IndexedChunk, float]]: ...


class InMemoryDocumentStore:
    """Non-durable adapter retained for isolated tests and development."""

    def __init__(self):
        self._documents: dict[tuple[str, int], Document] = {}
        self._chunks: dict[UUID, list[Chunk]] = {}
        self._tombstoned: set[str] = set()

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

        self.engine: AsyncEngine = create_async_engine(
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
        missing = {"ensemble_documents", "ensemble_chunks", "ensemble_vector_collections", "ensemble_vector_records", "ensemble_index_sources", "ensemble_knowledge_records", "ensemble_graph_nodes", "ensemble_graph_edges", "ensemble_memories", "ensemble_memory_entries", "ensemble_memory_sources", "ensemble_ga_runs", "ensemble_ga_promotions", "ensemble_thompson_models", "ensemble_thompson_events", "ensemble_learning_outcomes", "ensemble_learning_artifacts", "ensemble_lexical_fts", "ensemble_transcript_checkpoints", "ensemble_transcript_jobs"} - set(tables)
        if missing:
            raise RuntimeError(
                "Ensemble database schema is incomplete; run 'alembic upgrade head'"
            )
        async with self.engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != "0016_transcript_jobs":
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
            await connection.execute(
                update(self.documents).where(self.documents.c.source_id == source_id)
                .values(is_current=False)
            )

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

