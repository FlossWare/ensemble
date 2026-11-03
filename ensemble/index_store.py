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
    Column("purged", Boolean, nullable=False, server_default="0"),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

generation_sources = Table(
    "ensemble_index_generation_sources",
    index_metadata,
    Column("generation_id", String(64), primary_key=True),
    Column("source_id", String(512), primary_key=True),
    Column("high_sequence", Integer, nullable=False),
    Column("revision_id", String(512), nullable=False),
    Column("profiles", JSON, nullable=False),
    Column("tombstoned", Boolean, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


generations = Table(
    "ensemble_index_generations",
    index_metadata,
    Column("generation_id", String(64), primary_key=True),
    Column("state", String(16), nullable=False),
    Column("checkpoints", JSON, nullable=False),
    Column("verification", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("activated_at", DateTime(timezone=True), nullable=True),
)
generation_control = Table(
    "ensemble_index_generation_control",
    index_metadata,
    Column("singleton_id", Integer, primary_key=True),
    Column("active_generation_id", String(64), nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


class IndexStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def record_revision(self, source_id: str, sequence: int, revision_id: str, profiles: dict[str, Any]) -> dict[str, Any]: ...
    async def record_tombstone(self, source_id: str, sequence: int) -> dict[str, Any]: ...
    async def get_source(self, source_id: str) -> dict[str, Any] | None: ...
    async def list_sources(self, limit: int) -> list[dict[str, Any]]: ...
    async def source_key_upper_bound(self) -> str | None: ...
    async def list_sources_after(self, cursor: str | None, upper_bound: str | None, limit: int) -> list[dict[str, Any]]: ...
    async def put_generation_source(self, generation_id: str, source: dict[str, Any]) -> dict[str, Any]: ...
    async def get_generation_source(self, generation_id: str, source_id: str) -> dict[str, Any] | None: ...
    async def list_generation_sources_after(self, generation_id: str, cursor: str | None, upper_bound: str | None, limit: int) -> list[dict[str, Any]]: ...
    async def generation_status(self) -> dict[str, Any]: ...
    async def create_generation(self, generation_id: str) -> dict[str, Any]: ...
    async def record_generation_checkpoint(self, generation_id: str, capability: str, checkpoint: dict[str, Any], sealed: bool = False) -> dict[str, Any]: ...
    async def activate_generation(self, generation_id: str, expected_active_generation_id: str, expected_epoch: int, verification: dict[str, Any]) -> dict[str, Any]: ...
    async def abort_generation(self, generation_id: str, reason: str) -> dict[str, Any]: ...


def _as_source(row: Any) -> dict[str, Any]:
    return {
        "source_id": row["source_id"], "high_sequence": row["high_sequence"],
        "revision_id": row["revision_id"], "profiles": row["profiles"],
        "tombstoned": bool(row["tombstoned"]), "purged": bool(row["purged"]), "updated_at": row["updated_at"].isoformat(),
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
        required = {
            "ensemble_index_sources", "ensemble_index_generations",
            "ensemble_index_generation_control",
        }
        if not required.issubset(names):
            raise RuntimeError("index schema is not initialized; run 'alembic upgrade head'")

    async def close(self) -> None:
        await self.engine.dispose()

    async def generation_status(self) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            control = (await connection.execute(select(generation_control).where(
                generation_control.c.singleton_id == 1
            ))).mappings().one()
            rows = (await connection.execute(
                select(generations).order_by(generations.c.created_at.desc())
            )).mappings().all()
        return {
            "active_generation_id": control["active_generation_id"],
            "epoch": control["epoch"],
            "generations": [dict(row) for row in rows],
        }

    async def create_generation(self, generation_id: str) -> dict[str, Any]:
        if not isinstance(generation_id, str) or not generation_id or len(generation_id) > 64:
            raise ValueError("generation_id must contain 1-64 characters")
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            existing = (await connection.execute(select(generations).where(
                generations.c.state == "building"
            ))).mappings().first()
            if existing:
                raise RuntimeError("an index generation rebuild is already in progress")
            await connection.execute(generations.insert().values(
                generation_id=generation_id, state="building", checkpoints={},
                verification={}, created_at=now, updated_at=now, activated_at=None,
            ))
            row = (await connection.execute(select(generations).where(
                generations.c.generation_id == generation_id
            ))).mappings().one()
        return dict(row)

    async def record_generation_checkpoint(
        self, generation_id: str, capability: str,
        checkpoint: dict[str, Any], sealed: bool = False,
    ) -> dict[str, Any]:
        if capability not in {"index", "documents", "vectors", "memory", "knowledge"}:
            raise ValueError("capability must be index, memory, or knowledge")
        if not isinstance(checkpoint, dict):
            raise ValueError("checkpoint must be an object")
        import json
        try:
            json.dumps(checkpoint, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("checkpoint must contain finite JSON values") from exc
        async with self.engine.begin() as connection:
            row = (await connection.execute(select(generations).where(
                generations.c.generation_id == generation_id
            ))).mappings().first()
            if row is None or row["state"] != "building":
                raise RuntimeError("generation is not in building state")
            checkpoints = dict(row["checkpoints"] or {})
            previous = checkpoints.get(capability)
            if previous and previous.get("sealed"):
                raise RuntimeError(f"{capability} checkpoint is already sealed")
            checkpoints[capability] = {"value": checkpoint, "sealed": bool(sealed)}
            await connection.execute(generations.update().where(
                generations.c.generation_id == generation_id,
                generations.c.state == "building",
            ).values(checkpoints=checkpoints, updated_at=datetime.now(timezone.utc)))
            updated = (await connection.execute(select(generations).where(
                generations.c.generation_id == generation_id
            ))).mappings().one()
        return dict(updated)

    async def activate_generation(
        self, generation_id: str, expected_active_generation_id: str,
        expected_epoch: int, verification: dict[str, Any],
    ) -> dict[str, Any]:
        required = {"acknowledged_writes_verified", "tombstones_verified"}
        if not isinstance(verification, dict) or not all(verification.get(key) is True for key in required):
            raise ValueError("activation requires acknowledged-write and tombstone verification")
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            current_control = (await connection.execute(select(generation_control).where(
                generation_control.c.singleton_id == 1
            ))).mappings().one()
            if (
                current_control["active_generation_id"] != expected_active_generation_id
                or current_control["epoch"] != expected_epoch
            ):
                raise RuntimeError("active generation changed; refresh status before cutover")
            target = (await connection.execute(select(generations).where(
                generations.c.generation_id == generation_id
            ))).mappings().first()
            if target is None or target["state"] != "building":
                raise RuntimeError("target generation is not building")
            checkpoints = target["checkpoints"] or {}
            if not all(checkpoints.get(name, {}).get("sealed") is True for name in ("index", "documents", "vectors", "memory", "knowledge")):
                raise RuntimeError("index, documents, vectors, memory, and knowledge checkpoints must all be sealed")
            changed = await connection.execute(generation_control.update().where(
                generation_control.c.singleton_id == 1,
                generation_control.c.active_generation_id == expected_active_generation_id,
                generation_control.c.epoch == expected_epoch,
            ).values(
                active_generation_id=generation_id, epoch=expected_epoch + 1,
                updated_at=now,
            ))
            if changed.rowcount != 1:
                raise RuntimeError("active generation changed; refresh status before cutover")
            await connection.execute(generations.update().where(
                generations.c.generation_id == expected_active_generation_id,
                generations.c.state == "active",
            ).values(state="retired", updated_at=now))
            await connection.execute(generations.update().where(
                generations.c.generation_id == generation_id,
                generations.c.state == "building",
            ).values(
                state="active", verification=verification,
                activated_at=now, updated_at=now,
            ))
            row = (await connection.execute(select(generation_control).where(
                generation_control.c.singleton_id == 1
            ))).mappings().one()
        return {
            "active_generation_id": row["active_generation_id"],
            "epoch": row["epoch"],
        }

    async def abort_generation(self, generation_id: str, reason: str) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2048:
            raise ValueError("reason must contain 1-2048 characters")
        async with self.engine.begin() as connection:
            changed = await connection.execute(generations.update().where(
                generations.c.generation_id == generation_id,
                generations.c.state == "building",
            ).values(
                state="failed", verification={"failure": reason},
                updated_at=datetime.now(timezone.utc),
            ))
            if changed.rowcount != 1:
                raise RuntimeError("only a building generation can be aborted")
            row = (await connection.execute(select(generations).where(
                generations.c.generation_id == generation_id
            ))).mappings().one()
        return dict(row)


    async def put_generation_source(self, generation_id: str, source: dict[str, Any]) -> dict[str, Any]:
        required = {"source_id", "high_sequence", "revision_id", "profiles", "tombstoned"}
        if not isinstance(source, dict) or not required.issubset(source):
            raise ValueError("source state is incomplete")
        source_id = source["source_id"]
        if not isinstance(source["profiles"], dict) or not isinstance(source["tombstoned"], bool):
            raise ValueError("profiles must be an object and tombstoned must be boolean")
        sequence = source["high_sequence"]
        revision_id = source["revision_id"]
        profiles = dict(sorted(source["profiles"].items()))
        tombstoned = bool(source["tombstoned"])
        if not isinstance(source_id, str) or not source_id or len(source_id) > 512:
            raise ValueError("source_id must contain 1-512 characters")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("high_sequence must be a positive integer")
        if not isinstance(revision_id, str) or not revision_id or len(revision_id) > 512:
            raise ValueError("revision_id must contain 1-512 characters")
        import json
        try:
            json.dumps(profiles, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("profiles must contain finite JSON values") from exc
        async with self.engine.begin() as connection:
            generation = (await connection.execute(select(generations).where(
                generations.c.generation_id == generation_id
            ))).mappings().first()
            if generation is None or generation["state"] != "building":
                raise RuntimeError("generation is not in building state")
            row = (await connection.execute(select(generation_sources).where(
                generation_sources.c.generation_id == generation_id,
                generation_sources.c.source_id == source_id,
            ))).mappings().first()
            now = datetime.now(timezone.utc)
            if row is None:
                await connection.execute(generation_sources.insert().values(
                    generation_id=generation_id, source_id=source_id,
                    high_sequence=sequence, revision_id=revision_id,
                    profiles=profiles, tombstoned=tombstoned, updated_at=now,
                ))
                decision = "accepted"
            elif row["purged"]:
                decision = "purged"
            elif row["tombstoned"]:
                decision = "tombstoned"
            elif tombstoned:
                await connection.execute(generation_sources.update().where(
                    generation_sources.c.generation_id == generation_id,
                    generation_sources.c.source_id == source_id,
                ).values(
                    high_sequence=max(sequence, row["high_sequence"]),
                    tombstoned=True, updated_at=now,
                ))
                decision = "tombstoned"
            elif sequence > row["high_sequence"]:
                await connection.execute(generation_sources.update().where(
                    generation_sources.c.generation_id == generation_id,
                    generation_sources.c.source_id == source_id,
                ).values(
                    high_sequence=sequence, revision_id=revision_id,
                    profiles=profiles, updated_at=now,
                ))
                decision = "accepted"
            elif sequence == row["high_sequence"]:
                decision = "duplicate" if row["revision_id"] == revision_id and row["profiles"] == profiles else "conflict"
            else:
                decision = "superseded"
            current = (await connection.execute(select(generation_sources).where(
                generation_sources.c.generation_id == generation_id,
                generation_sources.c.source_id == source_id,
            ))).mappings().one()
        return {
            "decision": decision, "source": {
                "source_id": current["source_id"],
                "high_sequence": current["high_sequence"],
                "revision_id": current["revision_id"],
                "profiles": current["profiles"],
                "tombstoned": bool(current["tombstoned"]),
                "updated_at": current["updated_at"].isoformat(),
            },
        }

    async def get_generation_source(self, generation_id: str, source_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(select(generation_sources).where(
                generation_sources.c.generation_id == generation_id,
                generation_sources.c.source_id == source_id,
            ))).mappings().first()
        if row is None:
            return None
        return {
            "source_id": row["source_id"], "high_sequence": row["high_sequence"],
            "revision_id": row["revision_id"], "profiles": row["profiles"],
            "tombstoned": bool(row["tombstoned"]),
            "updated_at": row["updated_at"].isoformat(),
        }

    async def list_generation_sources_after(
        self, generation_id: str, cursor: str | None,
        upper_bound: str | None, limit: int,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 10000:
            raise ValueError("limit must be between 1 and 10000")
        if upper_bound is None:
            return []
        statement = select(generation_sources).where(
            generation_sources.c.generation_id == generation_id,
            generation_sources.c.source_id <= upper_bound,
        )
        if cursor is not None:
            statement = statement.where(generation_sources.c.source_id > cursor)
        statement = statement.order_by(generation_sources.c.source_id).limit(limit)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [{
            "source_id": row["source_id"], "high_sequence": row["high_sequence"],
            "revision_id": row["revision_id"], "profiles": row["profiles"],
            "tombstoned": bool(row["tombstoned"]),
            "updated_at": row["updated_at"].isoformat(),
        } for row in rows]

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
            elif row["purged"]:
                decision = "purged"
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

    async def restore_source(self, source_id: str, sequence: int) -> dict[str, Any]:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("sequence must be a positive integer")
        async with self.engine.begin() as connection:
            row = (await connection.execute(select(sources).where(sources.c.source_id == source_id))).mappings().first()
            if row is None:
                raise KeyError("unknown source")
            if row["purged"]:
                raise RuntimeError("permanently purged sources cannot be restored")
            if sequence <= row["high_sequence"]:
                raise ValueError("restore sequence must exceed the current source sequence")
            await connection.execute(update(sources).where(
                sources.c.source_id == source_id
            ).values(high_sequence=sequence, tombstoned=False, updated_at=datetime.now(timezone.utc)))
            updated = (await connection.execute(select(sources).where(
                sources.c.source_id == source_id
            ))).mappings().one()
        return _as_source(updated)

    async def mark_purged(self, source_id: str) -> None:
        async with self.engine.begin() as connection:
            changed = await connection.execute(update(sources).where(
                sources.c.source_id == source_id,
                sources.c.tombstoned.is_(True),
                sources.c.purged.is_(False),
            ).values(purged=True, updated_at=datetime.now(timezone.utc)))
            if changed.rowcount != 1:
                raise RuntimeError("only a soft-deleted source can be permanently purged")

    async def list_sources(self, limit: int) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            rows = (await connection.execute(select(sources).order_by(sources.c.source_id).limit(limit))).mappings().all()
        return [_as_source(row) for row in rows]

    async def source_key_upper_bound(self) -> str | None:
        async with self.engine.connect() as connection:
            return await connection.scalar(select(__import__("sqlalchemy").func.max(sources.c.source_id)))

    async def list_sources_after(
        self, cursor: str | None, upper_bound: str | None, limit: int,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 10000:
            raise ValueError("limit must be between 1 and 10000")
        if upper_bound is None:
            return []
        statement = select(sources).where(sources.c.source_id <= upper_bound)
        if cursor is not None:
            statement = statement.where(sources.c.source_id > cursor)
        statement = statement.order_by(sources.c.source_id).limit(limit)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [_as_source(row) for row in rows]
