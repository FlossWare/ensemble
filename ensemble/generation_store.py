from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, MetaData, String, Table, insert, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


metadata = MetaData()
generations = Table(
    "ensemble_generations",
    metadata,
    Column("generation_id", String(128), primary_key=True),
    Column("state", String(24), nullable=False),
    Column("snapshot_watermark", Integer, nullable=True),
    Column("applied_watermark", Integer, nullable=True),
    Column("previous_generation_id", String(128), nullable=True),
    Column("failure_reason", String(2048), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
checkpoints = Table(
    "ensemble_generation_checkpoints",
    metadata,
    Column("generation_id", String(128), primary_key=True),
    Column("phase", String(32), primary_key=True),
    Column("cursor", JSON, nullable=False),
    Column("sealed", Boolean, nullable=False),
    Column("watermark", Integer, nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
control = Table(
    "ensemble_generation_control",
    metadata,
    Column("control_id", Integer, primary_key=True),
    Column("active_generation_id", String(128), nullable=True),
    Column("fenced_generation_id", String(128), nullable=True),
    Column("fence_watermark", Integer, nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

TRANSITIONS = {
    "building": {"reconciling", "failed", "aborted"},
    "reconciling": {"fenced", "failed", "aborted"},
    "fenced": {"verifying", "failed", "aborted"},
    "verifying": {"ready", "failed", "aborted"},
    "ready": {"active", "failed", "aborted"},
    "active": {"retired"},
    "retired": set(),
    "failed": set(),
    "aborted": set(),
}


class SqliteGenerationStore:
    """Durable generation lifecycle metadata and compare-and-swap activation."""

    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.getenv(
            "ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db"
        )
        if make_url(self.database_url).get_backend_name() != "sqlite":
            raise ValueError("Ensemble generation metadata supports SQLite only")
        self.engine: AsyncEngine = create_async_engine(self.database_url, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            names = await connection.run_sync(
                lambda conn: __import__("sqlalchemy").inspect(conn).get_table_names()
            )
        required = {"ensemble_generations", "ensemble_generation_checkpoints", "ensemble_generation_control"}
        missing = required - set(names)
        if missing:
            raise RuntimeError(
                "generation schema is not initialized; run 'alembic upgrade head' "
                f"(missing: {', '.join(sorted(missing))})"
            )

    async def close(self) -> None:
        await self.engine.dispose()

    async def create(
        self, generation_id: str, *, snapshot_watermark: int | None = None,
        previous_generation_id: str | None = None,
    ) -> dict[str, Any]:
        if not generation_id or len(generation_id) > 128:
            raise ValueError("generation_id must contain 1-128 characters")
        if snapshot_watermark is not None and snapshot_watermark < 0:
            raise ValueError("snapshot_watermark must be non-negative")
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            await connection.execute(insert(generations).values(
                generation_id=generation_id, state="building",
                snapshot_watermark=snapshot_watermark, applied_watermark=snapshot_watermark,
                previous_generation_id=previous_generation_id, failure_reason=None,
                created_at=now, updated_at=now,
            ))
            row = (await connection.execute(
                select(generations).where(generations.c.generation_id == generation_id)
            )).mappings().one()
        return dict(row)

    async def transition(
        self, generation_id: str, state: str, *, failure_reason: str | None = None,
        applied_watermark: int | None = None,
    ) -> dict[str, Any]:
        if state not in TRANSITIONS:
            raise ValueError(f"unknown generation state: {state}")
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            row = (await connection.execute(
                select(generations).where(generations.c.generation_id == generation_id)
            )).mappings().first()
            if row is None:
                raise KeyError(generation_id)
            if state not in TRANSITIONS[row["state"]]:
                raise ValueError(f"invalid generation transition: {row['state']} -> {state}")
            values: dict[str, Any] = {"state": state, "updated_at": now}
            if failure_reason is not None:
                values["failure_reason"] = failure_reason[:2048]
            if applied_watermark is not None:
                if applied_watermark < (row["applied_watermark"] or 0):
                    raise ValueError("applied_watermark cannot move backwards")
                values["applied_watermark"] = applied_watermark
            await connection.execute(update(generations).where(
                generations.c.generation_id == generation_id,
                generations.c.state == row["state"],
            ).values(**values))
            current = (await connection.execute(
                select(generations).where(generations.c.generation_id == generation_id)
            )).mappings().one()
        return dict(current)

    async def checkpoint(
        self, generation_id: str, phase: str, cursor: dict[str, Any], *,
        sealed: bool = False, watermark: int | None = None,
    ) -> dict[str, Any]:
        if not phase or len(phase) > 32:
            raise ValueError("phase must contain 1-32 characters")
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            generation = (await connection.execute(
                select(generations.c.state).where(generations.c.generation_id == generation_id)
            )).scalar_one_or_none()
            if generation not in {"building", "reconciling", "fenced", "verifying"}:
                raise ValueError("checkpoints can only change before activation")
            existing = (await connection.execute(select(checkpoints).where(
                checkpoints.c.generation_id == generation_id,
                checkpoints.c.phase == phase,
            ))).mappings().first()
            if existing and existing["sealed"] and (
                existing["cursor"] != cursor or existing["watermark"] != watermark
            ):
                raise ValueError("a sealed checkpoint is immutable")
            values = {
                "cursor": cursor, "sealed": bool(sealed),
                "watermark": watermark, "updated_at": now,
            }
            if existing:
                await connection.execute(update(checkpoints).where(
                    checkpoints.c.generation_id == generation_id,
                    checkpoints.c.phase == phase,
                ).values(**values))
            else:
                await connection.execute(insert(checkpoints).values(
                    generation_id=generation_id, phase=phase, **values,
                ))
            row = (await connection.execute(select(checkpoints).where(
                checkpoints.c.generation_id == generation_id,
                checkpoints.c.phase == phase,
            ))).mappings().one()
        return dict(row)

    async def status(self) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                select(generations).order_by(generations.c.created_at, generations.c.generation_id)
            )).mappings().all()
            checkpoint_rows = (await connection.execute(
                select(checkpoints).order_by(checkpoints.c.generation_id, checkpoints.c.phase)
            )).mappings().all()
            current = (await connection.execute(
                select(control).where(control.c.control_id == 1)
            )).mappings().first()
        grouped: dict[str, dict[str, Any]] = {}
        for item in checkpoint_rows:
            grouped.setdefault(item["generation_id"], {})[item["phase"]] = dict(item)
        return {
            "active_generation_id": current["active_generation_id"] if current else None,
            "fenced_generation_id": current["fenced_generation_id"] if current else None,
            "fence_watermark": current["fence_watermark"] if current else None,
            "generations": [
                {**dict(row), "checkpoints": grouped.get(row["generation_id"], {})}
                for row in rows
            ],
        }

    async def acquire_fence(self, generation_id: str, watermark: int) -> None:
        """Persist the cutover fence marker after the caller has stopped admitting writes."""
        if watermark < 0:
            raise ValueError("fence watermark must be non-negative")
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            candidate = (await connection.execute(select(generations).where(
                generations.c.generation_id == generation_id
            ))).mappings().first()
            if candidate is None:
                raise KeyError(generation_id)
            if candidate["state"] != "reconciling":
                raise ValueError("a generation must be reconciling before the cutover fence")
            if watermark < (candidate["applied_watermark"] or 0):
                raise ValueError("fence watermark cannot precede the applied watermark")
            current = (await connection.execute(select(control).where(
                control.c.control_id == 1
            ))).mappings().first()
            if current and current["fenced_generation_id"] not in {None, generation_id}:
                raise ValueError("another generation currently owns the cutover fence")
            if current is None:
                await connection.execute(insert(control).values(
                    control_id=1, active_generation_id=None,
                    fenced_generation_id=generation_id, fence_watermark=watermark,
                    updated_at=now,
                ))
            else:
                await connection.execute(update(control).where(
                    control.c.control_id == 1,
                ).values(
                    fenced_generation_id=generation_id,
                    fence_watermark=watermark, updated_at=now,
                ))
            await connection.execute(update(generations).where(
                generations.c.generation_id == generation_id,
                generations.c.state == "reconciling",
            ).values(state="fenced", updated_at=now))

    async def activate(self, generation_id: str, *, expected_active_generation_id: str | None) -> dict[str, Any]:
        """Atomically compare-and-swap the active pointer and retire its predecessor."""
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            candidate = (await connection.execute(select(generations).where(
                generations.c.generation_id == generation_id
            ))).mappings().first()
            if candidate is None:
                raise KeyError(generation_id)
            if candidate["state"] != "ready":
                raise ValueError("only a ready generation can be activated")
            current = (await connection.execute(select(control).where(
                control.c.control_id == 1
            ))).mappings().first()
            if current is None or current["fenced_generation_id"] != generation_id:
                raise ValueError("generation must own the cutover fence before activation")
            if current["fence_watermark"] != candidate["applied_watermark"]:
                raise ValueError("candidate is not reconciled through the cutover fence watermark")
            verification = (await connection.execute(select(checkpoints).where(
                checkpoints.c.generation_id == generation_id,
                checkpoints.c.phase == "verification",
            ))).mappings().first()
            if (
                verification is None or not verification["sealed"]
                or verification["watermark"] != current["fence_watermark"]
            ):
                raise ValueError("candidate verification must be sealed at the fence watermark")
            actual_active = current["active_generation_id"] if current else None
            if actual_active != expected_active_generation_id:
                raise ValueError("active generation changed; candidate must be revalidated")
            if current is None:
                await connection.execute(insert(control).values(
                    control_id=1, active_generation_id=generation_id,
                    fenced_generation_id=None, fence_watermark=None, updated_at=now,
                ))
            else:
                result = await connection.execute(update(control).where(
                    control.c.control_id == 1,
                    control.c.active_generation_id.is_(expected_active_generation_id)
                    if expected_active_generation_id is None
                    else control.c.active_generation_id == expected_active_generation_id,
                ).values(active_generation_id=generation_id, fenced_generation_id=None,
                         fence_watermark=None, updated_at=now))
                if result.rowcount != 1:
                    raise ValueError("active generation changed during activation")
            if actual_active:
                await connection.execute(update(generations).where(
                    generations.c.generation_id == actual_active,
                    generations.c.state == "active",
                ).values(state="retired", updated_at=now))
            await connection.execute(update(generations).where(
                generations.c.generation_id == generation_id,
                generations.c.state == "ready",
            ).values(state="active", updated_at=now))
        return {"active_generation_id": generation_id, "previous_generation_id": actual_active}
