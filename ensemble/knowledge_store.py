from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, MetaData, String, Table, Text, select, text
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
    Column("generation_id", String(64), ForeignKey("ensemble_index_generations.generation_id", ondelete="CASCADE"), primary_key=True),
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
    async def restore(self, source_id: str) -> None: ...
    async def reclaim_source(self, source_id: str) -> int: ...
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
        if not {"ensemble_knowledge_records", "ensemble_knowledge_generation_records"}.issubset(names):
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
        if row is None or row["tombstoned"]:
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

    async def restore(self, source_id: str) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(knowledge_records.update().where(knowledge_records.c.source_id == source_id).values(tombstoned=False, updated_at=datetime.now(timezone.utc)))

    async def reclaim_source(self, source_id: str) -> int:
        async with self.engine.begin() as connection:
            result = await connection.execute(knowledge_records.delete().where(knowledge_records.c.source_id == source_id))
        return result.rowcount or 0


    async def upsert_generation(self, generation_id: str, record: dict[str, Any]) -> str:
        digest = hashlib.sha256(record["content"].encode("utf-8")).hexdigest()
        provenance = record.get("provenance") or {}
        origin = record.get("origin") or provenance.get("origin")
        locator = record.get("locator") or provenance.get("locator")
        if not isinstance(origin, str) or not isinstance(locator, str):
            raise ValueError("Knowledge record provenance is incomplete")
        values = {
            "generation_id": generation_id,
            "source_id": record["source_id"],
            "revision_sequence": record["revision_sequence"],
            "revision_id": record["revision_id"],
            "title": record["title"],
            "content": record["content"],
            "content_sha256": digest,
            "origin": origin,
            "locator": locator,
            "profiles": record["profiles"],
            "tombstoned": False,
            "updated_at": datetime.now(timezone.utc),
        }
        async with self.engine.begin() as connection:
            exists = await connection.scalar(
                text("SELECT COUNT(*) FROM ensemble_index_generations WHERE generation_id = :generation_id"),
                {"generation_id": generation_id},
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
        return copy.deepcopy(record) if record and not record.get("tombstoned") else None

    async def tombstone(self, source_id: str) -> None:
        if source_id in self._records:
            self._records[source_id]["tombstoned"] = True

    async def restore(self, source_id: str) -> None:
        if source_id in self._records:
            self._records[source_id]["tombstoned"] = False

    async def reclaim_source(self, source_id: str) -> int:
        return int(self._records.pop(source_id, None) is not None)

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

