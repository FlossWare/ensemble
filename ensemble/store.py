from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, MetaData,
    String, Table, Text, UniqueConstraint, TypeDecorator, delete, func, insert,
    inspect, select, text, update,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.engine import make_url
from pgvector.sqlalchemy import Vector

from .models import Chunk, Document


class EmbeddingVector(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector())
        return dialect.type_descriptor(JSON())


metadata = MetaData()
documents = Table(
    "ensemble_documents",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("source_id", String(1024), nullable=False),
    Column("partition_id", String(255), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("title", String(2048), nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("is_current", Boolean, nullable=False, default=True),
    UniqueConstraint("partition_id", "source_id", "revision", name="uq_ensemble_document_partition_source_revision"),
)
chunks = Table(
    "ensemble_chunks",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("document_id", String(36), ForeignKey("ensemble_documents.id", ondelete="CASCADE"), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("content", Text, nullable=False),
    Column("embedding", JSON, nullable=False),
    Column("embedding_vector", EmbeddingVector(), nullable=True),
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
    async def all_chunks(self, query: str | None = None, limit: int | None = None, partition_id: str = "default") -> list[IndexedChunk]: ...
    async def vector_candidates(self, vector: list[float], limit: int, partition_id: str = "default") -> list[IndexedChunk]: ...


class InMemoryDocumentStore:
    """Non-durable adapter retained for isolated tests and development."""

    def __init__(self):
        self._documents: dict[tuple[str, str, int], Document] = {}
        self._chunks: dict[UUID, list[Chunk]] = {}

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def check_ready(self) -> None:
        return None

    async def save(self, document: Document, chunks: list[Chunk]) -> Document:
        key = (document.partition_id, document.source_id, document.revision)
        previous = self._documents.get(key)
        if previous is not None:
            document.id = previous.id
            document.created_at = previous.created_at
            self._chunks.pop(previous.id, None)
        self._documents[key] = document
        self._chunks[document.id] = list(chunks)
        return document

    async def all_chunks(self, query: str | None = None, limit: int | None = None, partition_id: str = "default") -> list[IndexedChunk]:
        latest: dict[tuple[str, str], Document] = {}
        for document in self._documents.values():
            current = latest.get((document.partition_id, document.source_id))
            if current is None or document.revision > current.revision:
                latest[(document.partition_id, document.source_id)] = document
        terms = {term.casefold() for term in (query or "").split() if term}
        result: list[IndexedChunk] = []
        for document in latest.values():
            if document.partition_id != partition_id:
                continue
            for chunk in self._chunks.get(document.id, []):
                if terms and not any(term in chunk.content.casefold() for term in terms):
                    continue
                result.append(IndexedChunk(document, chunk))
        result.sort(key=lambda item: (item.document.source_id, item.chunk.ordinal))
        return result[:limit] if limit is not None else result

    async def vector_candidates(self, vector: list[float], limit: int, partition_id: str = "default") -> list[IndexedChunk]:
        candidates = await self.all_chunks(partition_id=partition_id)
        return sorted(
            candidates, key=lambda item: _cosine_distance(vector, item.chunk.embedding)
        )[:limit]


class SqlDocumentStore:
    """Durable SQL store. SQLite is the local default; PostgreSQL uses pgvector."""

    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.getenv(
            "ENSEMBLE_DATABASE_URL",
            "sqlite+aiosqlite:///./.ensemble/ensemble.db",
        )
        url = make_url(self.database_url)
        if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
            Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.engine: AsyncEngine = create_async_engine(self.database_url, pool_pre_ping=True)
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
        missing = {"ensemble_documents", "ensemble_chunks"} - set(tables)
        if missing:
            raise RuntimeError(
                "Ensemble database schema is incomplete; run 'alembic upgrade head'"
            )
        async with self.engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != "0003_partition_scope":
            raise RuntimeError(
                "Ensemble database schema revision is not supported; run 'alembic upgrade head'"
            )

    async def close(self) -> None:
        await self.engine.dispose()

    async def check_ready(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(select(1))

    async def save(self, document: Document, chunks: list[Chunk]) -> Document:
        backend = self.engine.url.get_backend_name()
        if backend == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        elif backend == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        else:
            raise RuntimeError(f"Unsupported database backend: {backend}")

        async with self.engine.begin() as connection:
            statement = dialect_insert(self.documents).values(
                id=str(document.id),
                source_id=document.source_id,
                partition_id=document.partition_id,
                revision=document.revision,
                title=document.title,
                content=document.content,
                created_at=document.created_at,
                is_current=False,
            ).on_conflict_do_nothing(index_elements=["partition_id", "source_id", "revision"])
            await connection.execute(statement)

            existing = (
                await connection.execute(
                    select(self.documents).where(
                        self.documents.c.partition_id == document.partition_id,
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
                .values(title=document.title, content=document.content, is_current=False)
            )
            await connection.execute(
                update(self.documents)
                .where(
                    self.documents.c.partition_id == document.partition_id,
                    self.documents.c.source_id == document.source_id,
                )
                .values(is_current=False)
            )
            latest_revision = await connection.scalar(
                select(func.max(self.documents.c.revision)).where(
                    self.documents.c.partition_id == document.partition_id,
                    self.documents.c.source_id == document.source_id,
                )
            )
            await connection.execute(
                update(self.documents)
                .where(
                    self.documents.c.partition_id == document.partition_id,
                    self.documents.c.source_id == document.source_id,
                    self.documents.c.revision == latest_revision,
                )
                .values(is_current=True)
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
                        "embedding_vector": chunk.embedding or None,
                    }
                    for chunk in chunks
                ])
        return document

    def _indexed_rows(self, rows) -> list[IndexedChunk]:
        result: list[IndexedChunk] = []
        for row in rows:
            document = Document(
                row["source_id"], row["revision"], row["title"], row["document_content"], row["partition_id"]
            )
            document.id = UUID(row["document_id"])
            document.created_at = row["created_at"]
            chunk = Chunk(
                document.id, row["ordinal"], row["chunk_content"], row["embedding"]
            )
            chunk.id = UUID(row["chunk_id"])
            result.append(IndexedChunk(document, chunk))
        return result

    def _chunk_select(self, partition_id: str):
        return (
            select(
                self.documents.c.id.label("document_id"),
                self.documents.c.source_id,
                self.documents.c.partition_id,
                self.documents.c.revision,
                self.documents.c.title,
                self.documents.c.content.label("document_content"),
                self.documents.c.created_at,
                self.chunks.c.id.label("chunk_id"),
                self.chunks.c.ordinal,
                self.chunks.c.content.label("chunk_content"),
                self.chunks.c.embedding,
            )
            .join(self.chunks, self.documents.c.id == self.chunks.c.document_id)
            .where(
                self.documents.c.is_current.is_(True),
                self.documents.c.partition_id == partition_id,
            )
        )

    async def all_chunks(self, query: str | None = None, limit: int | None = None, partition_id: str = "default") -> list[IndexedChunk]:
        statement = self._chunk_select(partition_id).order_by(
            self.documents.c.source_id, self.chunks.c.ordinal
        )
        if query and query.strip():
            from sqlalchemy import or_
            terms = [term for term in query.split() if term]
            statement = statement.where(or_(*[
                self.chunks.c.content.ilike(f"%{term}%") for term in terms
            ]))
        if limit is not None:
            statement = statement.limit(limit)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return self._indexed_rows(rows)

    async def vector_candidates(self, vector: list[float], limit: int, partition_id: str = "default") -> list[IndexedChunk]:
        if not vector:
            return []
        if self.engine.url.get_backend_name() == "postgresql":
            statement = (
                self._chunk_select(partition_id)
                .where(self.chunks.c.embedding_vector.is_not(None))
                .order_by(self.chunks.c.embedding_vector.cosine_distance(vector))
                .limit(limit)
            )
            async with self.engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
            return self._indexed_rows(rows)
        candidates = await self.all_chunks(partition_id=partition_id)
        return sorted(
            candidates, key=lambda item: _cosine_distance(vector, item.chunk.embedding)
        )[:limit]


def _cosine_distance(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 1.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if not left_norm or not right_norm:
        return 1.0
    return 1.0 - dot / (left_norm * right_norm)
