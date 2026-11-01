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
    async def all_chunks(self) -> list[IndexedChunk]: ...


class InMemoryDocumentStore:
    """Non-durable adapter retained for isolated tests and development."""

    def __init__(self):
        self._documents: dict[tuple[str, int], Document] = {}
        self._chunks: dict[UUID, list[Chunk]] = {}

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def check_ready(self) -> None:
        return None

    async def save(self, document: Document, chunks: list[Chunk]) -> Document:
        key = (document.source_id, document.revision)
        previous = self._documents.get(key)
        if previous is not None:
            document.id = previous.id
            document.created_at = previous.created_at
            self._chunks.pop(previous.id, None)
        self._documents[key] = document
        self._chunks[document.id] = list(chunks)
        return document

    async def all_chunks(self) -> list[IndexedChunk]:
        latest: dict[str, Document] = {}
        for document in self._documents.values():
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
        missing = {"ensemble_documents", "ensemble_chunks"} - set(tables)
        if missing:
            raise RuntimeError(
                "Ensemble database schema is incomplete; run 'alembic upgrade head'"
            )
        async with self.engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != "0001_initial":
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
                .values(title=document.title, content=document.content, is_current=False)
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
                    }
                    for chunk in chunks
                ])
        return document

    async def all_chunks(self) -> list[IndexedChunk]:
        statement = (
            select(
                self.documents.c.id.label("document_id"),
                self.documents.c.source_id,
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
            .where(self.documents.c.is_current.is_(True))
            .order_by(self.documents.c.source_id, self.chunks.c.ordinal)
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()

        result: list[IndexedChunk] = []
        for row in rows:
            document = Document(
                row["source_id"], row["revision"], row["title"], row["document_content"]
            )
            document.id = UUID(row["document_id"])
            document.created_at = row["created_at"]
            chunk = Chunk(
                document.id, row["ordinal"], row["chunk_content"], row["embedding"]
            )
            chunk.id = UUID(row["chunk_id"])
            result.append(IndexedChunk(document, chunk))
        return result
