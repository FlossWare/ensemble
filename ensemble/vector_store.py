from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, MetaData, String, Table, Text, delete, func, or_, select, text
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
generation_records = Table(
    "ensemble_vector_generation_records",
    vector_metadata,
    Column("collection_name", String(128), ForeignKey("ensemble_vector_collections.name", ondelete="CASCADE"), primary_key=True),
    Column("generation_id", String(64), ForeignKey("ensemble_index_generations.generation_id", ondelete="CASCADE"), primary_key=True),
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
    async def upsert_generation(self, generation_id: str, name: str, model: str, dimensions: int, items: list[dict[str, Any]]) -> int: ...
    async def query_generation(self, generation_id: str, name: str, embedding: list[float], top_k: int) -> list[dict[str, Any]]: ...
    async def list_records_for_source(self, name: str, source_id: str, limit: int = 10000) -> list[dict[str, Any]]:
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("source_id is required")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000")
        statement = select(
            records.c.record_id, records.c.embedding,
            records.c.document, records.c.metadata,
        ).where(
            records.c.collection_name == name,
            or_(
                func.json_extract(records.c.metadata, "$.source_id") == source_id,
                func.json_extract(records.c.metadata, "$.url") == source_id,
            ),
        ).order_by(records.c.record_id).limit(limit + 1)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        if len(rows) > limit:
            raise RuntimeError("source has too many vector records for one reconciliation page")
        return [{
            "id": row["record_id"], "embedding": row["embedding"],
            "document": row["document"], "metadata": row["metadata"],
        } for row in rows]

    async def delete_generation_source(self, generation_id: str, source_id: str) -> int:
        statement = delete(generation_records).where(
            generation_records.c.generation_id == generation_id,
            or_(
                func.json_extract(generation_records.c.metadata, "$.source_id") == source_id,
                func.json_extract(generation_records.c.metadata, "$.url") == source_id,
            ),
        )
        async with self.engine.begin() as connection:
            result = await connection.execute(statement)
        return int(result.rowcount or 0)

    async def tombstone_generation_source(self, generation_id: str, source_id: str) -> int:
        return await self.delete_generation_source(generation_id, source_id)

    async def delete_generation(self, generation_id: str) -> int: ...
    async def record_key_upper_bound(self, name: str) -> str | None: ...
    async def list_records_after(self, name: str, cursor: str | None, upper_bound: str | None, limit: int) -> list[dict[str, Any]]: ...


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
        if not {"ensemble_vector_collections", "ensemble_vector_records", "ensemble_vector_generation_records"}.issubset(names):
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


    async def record_key_upper_bound(self, name: str) -> str | None:
        async with self.engine.connect() as connection:
            return await connection.scalar(
                select(__import__("sqlalchemy").func.max(records.c.record_id)).where(
                    records.c.collection_name == name
                )
            )

    async def list_records_after(
        self, name: str, cursor: str | None,
        upper_bound: str | None, limit: int,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 10000:
            raise ValueError("limit must be between 1 and 10000")
        if upper_bound is None:
            return []
        statement = select(
            records.c.record_id, records.c.embedding,
            records.c.document, records.c.metadata,
        ).where(
            records.c.collection_name == name,
            records.c.record_id <= upper_bound,
        )
        if cursor is not None:
            statement = statement.where(records.c.record_id > cursor)
        statement = statement.order_by(records.c.record_id).limit(limit)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [{
            "id": row["record_id"], "embedding": row["embedding"],
            "document": row["document"], "metadata": row["metadata"],
        } for row in rows]

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

    async def upsert_generation(
        self, generation_id: str, name: str, model: str,
        dimensions: int, items: list[dict[str, Any]],
    ) -> int:
        if not generation_id or len(generation_id) > 64:
            raise ValueError("generation_id must contain 1-64 characters")
        async with self.engine.begin() as connection:
            generation = await connection.scalar(
                text("SELECT COUNT(*) FROM ensemble_index_generations WHERE generation_id = :generation_id"),
                {"generation_id": generation_id},
            )
            if not generation:
                raise ValueError("unknown index generation")
            await connection.execute(
                sqlite_insert(collections).values(
                    name=name, model=model, dimensions=dimensions,
                    created_at=datetime.now(timezone.utc),
                ).on_conflict_do_nothing(index_elements=["name"])
            )
            collection = (await connection.execute(
                select(collections).where(collections.c.name == name)
            )).mappings().one()
            if collection["model"] != model or collection["dimensions"] != dimensions:
                raise RuntimeError("vector collection model or dimension mismatch")
            if items:
                statement = sqlite_insert(generation_records).values([{
                    "collection_name": name, "generation_id": generation_id,
                    "record_id": item["id"], "embedding": item["embedding"],
                    "document": item["document"], "metadata": item["metadata"],
                } for item in items])
                await connection.execute(statement.on_conflict_do_update(
                    index_elements=["collection_name", "generation_id", "record_id"],
                    set_={
                        "embedding": statement.excluded.embedding,
                        "document": statement.excluded.document,
                        "metadata": statement.excluded.metadata,
                    },
                ))
        return len(items)

    async def query_generation(
        self, generation_id: str, name: str,
        embedding: list[float], top_k: int,
    ) -> list[dict[str, Any]]:
        if top_k < 1:
            return []
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                select(
                    generation_records.c.record_id, generation_records.c.embedding,
                    generation_records.c.document, generation_records.c.metadata,
                ).where(
                    generation_records.c.generation_id == generation_id,
                    generation_records.c.collection_name == name,
                )
            )).mappings().all()
        scored = []
        for row in rows:
            if len(row["embedding"]) != len(embedding):
                continue
            score = sum(left * right for left, right in zip(row["embedding"], embedding))
            scored.append({
                "id": row["record_id"], "document": row["document"],
                "score": float(score), "metadata": row["metadata"],
            })
        scored.sort(key=lambda item: (-item["score"], item["id"]))
        return scored[:top_k]

    async def tombstone_generation_source(self, generation_id: str, source_id: str) -> int:
        async with self.engine.begin() as connection:
            rows = (await connection.execute(
                select(
                    generation_records.c.collection_name,
                    generation_records.c.record_id,
                    generation_records.c.metadata,
                ).where(generation_records.c.generation_id == generation_id)
            )).mappings().all()
            matches = [
                (row["collection_name"], row["record_id"])
                for row in rows
                if row["metadata"].get("source_id") == source_id
                or row["metadata"].get("url") == source_id
            ]
            for collection_name, record_id in matches:
                await connection.execute(delete(generation_records).where(
                    generation_records.c.generation_id == generation_id,
                    generation_records.c.collection_name == collection_name,
                    generation_records.c.record_id == record_id,
                ))
        return len(matches)

    async def delete_generation(self, generation_id: str) -> int:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                delete(generation_records).where(
                    generation_records.c.generation_id == generation_id
                )
            )
        return result.rowcount or 0

