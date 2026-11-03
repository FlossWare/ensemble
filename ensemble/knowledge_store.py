from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, MetaData, String, Table, Text, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.engine import make_url


knowledge_metadata = MetaData()
knowledge_records = Table(
    "ensemble_knowledge_records",
    knowledge_metadata,
    Column("source_id", String(512), primary_key=True),
    Column("revision_sequence", Integer, nullable=False),
    Column("revision_id", String(512), nullable=False),
    Column("title", String(2048), nullable=False),
    Column("content", Text, nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("origin", String(512), nullable=False),
    Column("locator", String(2048), nullable=False),
    Column("profiles", JSON, nullable=False),
    Column("tombstoned", Boolean, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

knowledge_generation_records = Table(
    "ensemble_knowledge_generation_records",
    knowledge_metadata,
    Column("generation_id", String(64), primary_key=True),
    Column("source_id", String(512), primary_key=True),
    Column("revision_sequence", Integer, nullable=False),
    Column("revision_id", String(512), nullable=False),
    Column("title", String(2048), nullable=False),
    Column("content", Text, nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("origin", String(512), nullable=False),
    Column("locator", String(2048), nullable=False),
    Column("profiles", JSON, nullable=False),
    Column("tombstoned", Boolean, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

class KnowledgeStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def put(self, record: dict[str, Any]) -> str: ...
    async def get(self, source_id: str) -> dict[str, Any] | None: ...
    async def tombstone(self, source_id: str) -> None: ...
    async def upsert_generation(self, generation_id: str, record: dict[str, Any]) -> str: ...
    async def tombstone_generation_source(self, generation_id: str, source_id: str) -> None: ...
    async def get_generation(self, generation_id: str, source_id: str) -> dict[str, Any] | None: ...
    async def delete_generation(self, generation_id: str) -> int: ...


class SqliteKnowledgeStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.getenv("ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db")
        if make_url(self.database_url).get_backend_name() != "sqlite":
            raise ValueError("Ensemble knowledge persistence supports SQLite only")
        self.engine: AsyncEngine = create_async_engine(self.database_url, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            names = await connection.run_sync(lambda conn: __import__("sqlalchemy").inspect(conn).get_table_names())
        if "ensemble_knowledge_records" not in names:
            raise RuntimeError("knowledge schema is not initialized; run 'alembic upgrade head'")

    async def close(self) -> None:
        await self.engine.dispose()

    async def put(self, record: dict[str, Any]) -> str:
        digest = hashlib.sha256(record["content"].encode("utf-8")).hexdigest()
        values = {
            **record,
            "content_sha256": digest,
            "tombstoned": False,
            "updated_at": datetime.now(timezone.utc),
        }
        async with self.engine.begin() as connection:
            statement = sqlite_insert(knowledge_records).values(**values)
            await connection.execute(statement.on_conflict_do_update(
                index_elements=["source_id"],
                set_={key: statement.excluded[key] for key in (
                    "revision_sequence", "revision_id", "title", "content",
                    "content_sha256", "origin", "locator", "profiles",
                    "tombstoned", "updated_at",
                )},
            ))
        return digest

    async def get(self, source_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(
                select(knowledge_records).where(knowledge_records.c.source_id == source_id)
            )).mappings().first()
        if row is None:
            return None
        return {
            "source_id": row["source_id"],
            "revision_sequence": row["revision_sequence"],
            "revision_id": row["revision_id"],
            "title": row["title"],
            "content": row["content"],
            "content_sha256": row["content_sha256"],
            "provenance": {"origin": row["origin"], "locator": row["locator"]},
            "profiles": row["profiles"],
            "tombstoned": bool(row["tombstoned"]),
        }

    async def tombstone(self, source_id: str) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                knowledge_records.update().where(
                    knowledge_records.c.source_id == source_id
                ).values(tombstoned=True, updated_at=datetime.now(timezone.utc))
            )


    async def upsert_generation(self, generation_id: str, record: dict[str, Any]) -> str:
        digest = hashlib.sha256(record["content"].encode("utf-8")).hexdigest()
        values = {
            "generation_id": generation_id,
            "source_id": record["source_id"],
            "revision_sequence": record["revision_sequence"],
            "revision_id": record["revision_id"],
            "title": record["title"],
            "content": record["content"],
            "content_sha256": digest,
            "origin": record["origin"],
            "locator": record["locator"],
            "profiles": record["profiles"],
            "tombstoned": False,
            "updated_at": datetime.now(timezone.utc),
        }
        async with self.engine.begin() as connection:
            exists = await connection.scalar(
                select(__import__("sqlalchemy").func.count()).select_from(
                    __import__("sqlalchemy").table("ensemble_index_generations")
                ).where(__import__("sqlalchemy").column("generation_id") == generation_id)
            )
            if not exists:
                raise ValueError("unknown index generation")
            statement = sqlite_insert(knowledge_generation_records).values(**values)
            await connection.execute(statement.on_conflict_do_update(
                index_elements=["generation_id", "source_id"],
                set_={key: statement.excluded[key] for key in (
                    "revision_sequence", "revision_id", "title", "content",
                    "content_sha256", "origin", "locator", "profiles",
                    "tombstoned", "updated_at",
                )},
            ))
        return digest

    async def tombstone_generation_source(self, generation_id: str, source_id: str) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(knowledge_generation_records.update().where(
                knowledge_generation_records.c.generation_id == generation_id,
                knowledge_generation_records.c.source_id == source_id,
            ).values(tombstoned=True, updated_at=datetime.now(timezone.utc)))

    async def get_generation(self, generation_id: str, source_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(select(knowledge_generation_records).where(
                knowledge_generation_records.c.generation_id == generation_id,
                knowledge_generation_records.c.source_id == source_id,
            ))).mappings().first()
        if row is None:
            return None
        return {
            "source_id": row["source_id"], "revision_sequence": row["revision_sequence"],
            "revision_id": row["revision_id"], "title": row["title"],
            "content": row["content"], "content_sha256": row["content_sha256"],
            "provenance": {"origin": row["origin"], "locator": row["locator"]},
            "origin": row["origin"], "locator": row["locator"],
            "profiles": row["profiles"], "tombstoned": bool(row["tombstoned"]),
        }

    async def delete_generation(self, generation_id: str) -> int:
        from sqlalchemy import delete
        async with self.engine.begin() as connection:
            result = await connection.execute(delete(knowledge_generation_records).where(
                knowledge_generation_records.c.generation_id == generation_id
            ))
        return int(result.rowcount or 0)



class InMemoryKnowledgeStore:
    def __init__(self):
        self._records: dict[str, dict[str, Any]] = {}
        self._generation_records: dict[str, dict[str, dict[str, Any]]] = {}

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def put(self, record: dict[str, Any]) -> str:
        import copy
        digest = hashlib.sha256(record["content"].encode("utf-8")).hexdigest()
        self._records[record["source_id"]] = {
            **copy.deepcopy(record), "content_sha256": digest,
            "tombstoned": False,
        }
        return digest

    async def get(self, source_id: str) -> dict[str, Any] | None:
        import copy
        record = self._records.get(source_id)
        return copy.deepcopy(record) if record else None

    async def tombstone(self, source_id: str) -> None:
        if source_id in self._records:
            self._records[source_id]["tombstoned"] = True

    async def upsert_generation(self, generation_id: str, record: dict[str, Any]) -> str:
        import copy
        digest = hashlib.sha256(record["content"].encode("utf-8")).hexdigest()
        self._generation_records.setdefault(generation_id, {})[record["source_id"]] = {
            **copy.deepcopy(record), "content_sha256": digest, "tombstoned": False,
        }
        return digest

    async def tombstone_generation_source(self, generation_id: str, source_id: str) -> None:
        record = self._generation_records.get(generation_id, {}).get(source_id)
        if record is not None:
            record["tombstoned"] = True

    async def get_generation(self, generation_id: str, source_id: str) -> dict[str, Any] | None:
        import copy
        record = self._generation_records.get(generation_id, {}).get(source_id)
        return copy.deepcopy(record) if record else None

    async def delete_generation(self, generation_id: str) -> int:
        return len(self._generation_records.pop(generation_id, {}))

