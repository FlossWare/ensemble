from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, JSON, MetaData,
    String, Table, Text, UniqueConstraint, delete, func, insert, select, update,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.engine import make_url

from .models import Chunk, Document


@dataclass(frozen=True)
class IndexedChunk:
    document: Document
    chunk: Chunk


class DocumentStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
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
    """Durable SQL store. SQLite is the local default; PostgreSQL is supported."""

    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.getenv(
            "ENSEMBLE_DATABASE_URL",
            "sqlite+aiosqlite:///./.ensemble/ensemble.db",
        )
        url = make_url(self.database_url)
        if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
            Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

        self.engine: AsyncEngine = create_async_engine(
            self.database_url,
            pool_pre_ping=True,
        )
        self.metadata = MetaData()
        self.documents = Table(
            "ensemble_documents",
            self.metadata,
            Column("id", String(36), primary_key=True),
            Column("source_id", String(1024), nullable=False),
            Column("revision", Integer, nullable=False),
            Column("title", String(2048), nullable=False),
            Column("content", Text, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("is_current", Boolean, nullable=False, default=True),
            UniqueConstraint("source_id", "revision", name="uq_ensemble_document_source_revision"),
        )
        self.chunks = Table(
            "ensemble_chunks",
            self.metadata,
            Column("id", String(36), primary_key=True),
            Column("document_id", String(36), ForeignKey("ensemble_documents.id", ondelete="CASCADE"), nullable=False),
            Column("ordinal", Integer, nullable=False),
            Column("content", Text, nullable=False),
            Column("embedding", JSON, nullable=False),
            UniqueConstraint("document_id", "ordinal", name="uq_ensemble_chunk_document_ordinal"),
        )

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(self.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    async def save(self, document: Document, chunks: list[Chunk]) -> Document:
        backend = self.engine.url.get_backend_name()
        if backend == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        elif backend == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        else:
            raise RuntimeError(f"Unsupported database backend: {backend}")

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
