from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, MetaData, String, Table, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.engine import make_url


index_metadata = MetaData()
sources = Table(
    "ensemble_index_sources",
    index_metadata,
    Column("source_id", String(512), primary_key=True),
    Column("high_sequence", Integer, nullable=False),
    Column("revision_id", String(512), nullable=False),
    Column("profiles", JSON, nullable=False),
    Column("tombstoned", Boolean, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


class IndexStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def record_revision(self, source_id: str, sequence: int, revision_id: str, profiles: dict[str, Any]) -> dict[str, Any]: ...
    async def record_tombstone(self, source_id: str, sequence: int) -> dict[str, Any]: ...
    async def get_source(self, source_id: str) -> dict[str, Any] | None: ...
    async def list_sources(self, limit: int) -> list[dict[str, Any]]: ...


def _as_source(row: Any) -> dict[str, Any]:
    return {
        "source_id": row["source_id"], "high_sequence": row["high_sequence"],
        "revision_id": row["revision_id"], "profiles": row["profiles"],
        "tombstoned": bool(row["tombstoned"]), "updated_at": row["updated_at"].isoformat(),
    }


class SqliteIndexStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.getenv("ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db")
        if make_url(self.database_url).get_backend_name() != "sqlite":
            raise ValueError("Ensemble index persistence supports SQLite only")
        self.engine: AsyncEngine = create_async_engine(self.database_url, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            names = await connection.run_sync(lambda conn: __import__("sqlalchemy").inspect(conn).get_table_names())
        if "ensemble_index_sources" not in names:
            raise RuntimeError("index schema is not initialized; run 'alembic upgrade head'")

    async def close(self) -> None:
        await self.engine.dispose()

    async def record_revision(self, source_id: str, sequence: int, revision_id: str, profiles: dict[str, Any]) -> dict[str, Any]:
        async with self.engine.begin() as connection:
            row = (await connection.execute(select(sources).where(sources.c.source_id == source_id))).mappings().first()
            now = datetime.now(timezone.utc)
            profile_snapshot = dict(sorted(profiles.items()))
            if row is None:
                await connection.execute(sources.insert().values(
                    source_id=source_id, high_sequence=sequence, revision_id=revision_id,
                    profiles=profile_snapshot, tombstoned=False, updated_at=now,
                ))
                decision = "accepted"
            elif row["tombstoned"]:
                decision = "tombstoned"
            elif sequence > row["high_sequence"]:
                await connection.execute(update(sources).where(
                    sources.c.source_id == source_id
                ).values(
                    high_sequence=sequence, revision_id=revision_id,
                    profiles=profile_snapshot, updated_at=now,
                ))
                decision = "accepted"
            elif sequence == row["high_sequence"]:
                decision = "duplicate" if (
                    row["revision_id"] == revision_id and row["profiles"] == profile_snapshot
                ) else "conflict"
            else:
                decision = "superseded"
            current = (await connection.execute(select(sources).where(sources.c.source_id == source_id))).mappings().one()
        return {"decision": decision, "source": _as_source(current)}

    async def record_tombstone(self, source_id: str, sequence: int) -> dict[str, Any]:
        async with self.engine.begin() as connection:
            row = (await connection.execute(select(sources).where(sources.c.source_id == source_id))).mappings().first()
            now = datetime.now(timezone.utc)
            if row is None:
                await connection.execute(sources.insert().values(
                    source_id=source_id, high_sequence=sequence, revision_id="tombstone",
                    profiles={}, tombstoned=True, updated_at=now,
                ))
                decision = "tombstoned"
            elif sequence >= row["high_sequence"]:
                await connection.execute(update(sources).where(
                    sources.c.source_id == source_id
                ).values(
                    high_sequence=max(sequence, row["high_sequence"]),
                    tombstoned=True, updated_at=now,
                ))
                decision = "tombstoned"
            else:
                decision = "superseded"
            current = (await connection.execute(select(sources).where(sources.c.source_id == source_id))).mappings().one()
        return {"decision": decision, "source": _as_source(current)}

    async def get_source(self, source_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(select(sources).where(sources.c.source_id == source_id))).mappings().first()
        return _as_source(row) if row else None

    async def list_sources(self, limit: int) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            rows = (await connection.execute(select(sources).order_by(sources.c.source_id).limit(limit))).mappings().all()
        return [_as_source(row) for row in rows]
