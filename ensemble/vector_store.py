from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, MetaData, String, Table, Text, delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.engine import make_url


vector_metadata = MetaData()
collections = Table(
    "ensemble_vector_collections",
    vector_metadata,
    Column("name", String(128), primary_key=True),
    Column("model", String(512), nullable=False),
    Column("dimensions", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
records = Table(
    "ensemble_vector_records",
    vector_metadata,
    Column("collection_name", String(128), ForeignKey("ensemble_vector_collections.name", ondelete="CASCADE"), primary_key=True),
    Column("record_id", String(512), primary_key=True),
    Column("embedding", JSON, nullable=False),
    Column("document", Text, nullable=False),
    Column("metadata", JSON, nullable=False),
)


class VectorStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def list_collections(self) -> list[dict[str, Any]]: ...
    async def existing(self, name: str, ids: list[str]) -> list[str]: ...
    async def upsert(self, name: str, model: str, dimensions: int, items: list[dict[str, Any]]) -> int: ...
    async def update_metadata(self, name: str, items: list[dict[str, Any]]) -> int: ...
    async def delete_superseded(self, name: str, url: str, keep_sha256: str) -> int: ...
    async def query(self, name: str, embedding: list[float], top_k: int) -> list[dict[str, Any]]: ...


class SqliteVectorStore:
    """Exact vector operations persisted in Ensemble's SQLite database."""

    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.getenv("ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db")
        if make_url(self.database_url).get_backend_name() != "sqlite":
            raise ValueError("Ensemble vector storage supports SQLite only")
        self.engine: AsyncEngine = create_async_engine(self.database_url, pool_pre_ping=True)

    async def initialize(self) -> None:
        # The Alembic migration owns schema creation. Fail closed on missing vector tables.
        async with self.engine.connect() as connection:
            names = await connection.run_sync(lambda conn: __import__("sqlalchemy").inspect(conn).get_table_names())
        if not {"ensemble_vector_collections", "ensemble_vector_records"}.issubset(names):
            raise RuntimeError("vector schema is not initialized; run 'alembic upgrade head'")

    async def close(self) -> None:
        await self.engine.dispose()

    async def list_collections(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                select(collections.c.name, collections.c.model, collections.c.dimensions)
                .order_by(collections.c.name)
            )).mappings().all()
            result = []
            for row in rows:
                count = await connection.scalar(
                    select(__import__("sqlalchemy").func.count())
                    .select_from(records).where(records.c.collection_name == row["name"])
                )
                result.append({"name": row["name"], "model": row["model"], "dimensions": row["dimensions"], "count": count})
            return result

    async def existing(self, name: str, ids: list[str]) -> list[str]:
        if not ids:
            return []
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                select(records.c.record_id).where(
                    records.c.collection_name == name, records.c.record_id.in_(ids)
                )
            )).scalars().all()
        found = set(rows)
        return [record_id for record_id in ids if record_id in found]

    async def upsert(self, name: str, model: str, dimensions: int, items: list[dict[str, Any]]) -> int:
        from sqlalchemy import select as sql_select
        async with self.engine.begin() as connection:
            await connection.execute(
                sqlite_insert(collections).values(
                    name=name, model=model, dimensions=dimensions,
                    created_at=datetime.now(timezone.utc),
                ).on_conflict_do_nothing(index_elements=["name"])
            )
            collection = (await connection.execute(
                sql_select(collections).where(collections.c.name == name)
            )).mappings().one()
            if collection["model"] != model or collection["dimensions"] != dimensions:
                raise RuntimeError(
                    f"collection {name} uses model={collection['model']} dimensions={collection['dimensions']}; "
                    f"request uses model={model} dimensions={dimensions}"
                )
            if items:
                statement = sqlite_insert(records).values([
                    {
                        "collection_name": name,
                        "record_id": item["id"],
                        "embedding": item["embedding"],
                        "document": item["document"],
                        "metadata": item["metadata"],
                    } for item in items
                ])
                await connection.execute(statement.on_conflict_do_update(
                    index_elements=["collection_name", "record_id"],
                    set_={
                        "embedding": statement.excluded.embedding,
                        "document": statement.excluded.document,
                        "metadata": statement.excluded.metadata,
                    },
                ))
        return len(items)

    async def update_metadata(self, name: str, items: list[dict[str, Any]]) -> int:
        if not items:
            return 0
        async with self.engine.begin() as connection:
            updated = 0
            for item in items:
                result = await connection.execute(
                    records.update().where(
                        records.c.collection_name == name,
                        records.c.record_id == item["id"],
                    ).values(metadata=item["metadata"])
                )
                updated += result.rowcount or 0
        return updated

    async def delete_superseded(self, name: str, url: str, keep_sha256: str) -> int:
        async with self.engine.begin() as connection:
            rows = (await connection.execute(
                select(records.c.record_id, records.c.metadata).where(records.c.collection_name == name)
            )).all()
            stale = [
                row.record_id for row in rows
                if row.metadata.get("url") == url and row.metadata.get("content_sha256") != keep_sha256
            ]
            if stale:
                await connection.execute(delete(records).where(
                    records.c.collection_name == name, records.c.record_id.in_(stale)
                ))
        return len(stale)

    async def query(self, name: str, embedding: list[float], top_k: int) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                select(records.c.record_id, records.c.embedding, records.c.document, records.c.metadata)
                .where(records.c.collection_name == name)
            )).mappings().all()
        scored = []
        for row in rows:
            score = sum(left * right for left, right in zip(row["embedding"], embedding))
            scored.append({
                "id": row["record_id"], "document": row["document"],
                "score": float(score), "metadata": row["metadata"],
            })
        scored.sort(key=lambda item: (-item["score"], item["id"]))
        return scored[:top_k]
