from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, MetaData, String, Table, Text, UniqueConstraint, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.engine import make_url


ga_metadata = MetaData()
ga_runs = Table(
    "ensemble_ga_runs", ga_metadata,
    Column("id", String(36), primary_key=True),
    Column("status", String(32), nullable=False),
    Column("config", JSON, nullable=False),
    Column("result", JSON, nullable=True),
    Column("error", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
)
Index("ix_ensemble_ga_runs_status_created", ga_runs.c.status, ga_runs.c.created_at)
ga_promotions = Table(
    "ensemble_ga_promotions", ga_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), ForeignKey("ensemble_ga_runs.id", ondelete="CASCADE"), nullable=False),
    Column("system", String(32), nullable=False),
    Column("candidate", JSON, nullable=False),
    Column("reason", String(2048), nullable=False),
    Column("approved_by", String(256), nullable=False),
    Column("approved_at", DateTime(timezone=True), nullable=False),
    Column("status", String(32), nullable=False),
    UniqueConstraint("run_id", "system", name="uq_ensemble_ga_promotion_run_system"),
)


class GAStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def create_run(self, config: dict[str, Any]) -> dict[str, Any]: ...
    async def get_run(self, run_id: str) -> dict[str, Any] | None: ...
    async def list_runs(self, limit: int = 50) -> list[dict[str, Any]]: ...
    async def update_run(self, run_id: str, **values: Any) -> None: ...
    async def recover_interrupted_runs(self) -> int: ...
    async def promote(self, run_id: str, system: str, candidate: dict[str, Any], reason: str, approved_by: str) -> dict[str, Any]: ...


class SqliteGAStore:
    def __init__(self, database_url: str | None = None):
        url_text = database_url or os.getenv("ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db")
        if make_url(url_text).get_backend_name() != "sqlite":
            raise ValueError("Ensemble GA persistence supports SQLite only")
        self.engine: AsyncEngine = create_async_engine(url_text, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            from sqlalchemy import inspect
            names = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        if not {"ensemble_ga_runs", "ensemble_ga_promotions"}.issubset(names):
            raise RuntimeError("GA schema is not initialized; run 'alembic upgrade head'")

    async def close(self) -> None:
        await self.engine.dispose()

    async def recover_interrupted_runs(self) -> int:
        """Mark nonterminal runs interrupted by a previous process lifetime."""
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            result = await connection.execute(
                update(ga_runs)
                .where(ga_runs.c.status.in_(("pending", "running")))
                .values(
                    status="failed",
                    error="GA run interrupted by Ensemble process restart; rerun explicitly",
                    completed_at=now,
                )
            )
        return int(result.rowcount or 0)

    async def create_run(self, config: dict[str, Any]) -> dict[str, Any]:
        run_id, now = str(uuid4()), datetime.now(timezone.utc)
        row = {"id": run_id, "status": "pending", "config": config, "result": None,
               "error": None, "created_at": now, "started_at": None, "completed_at": None}
        async with self.engine.begin() as connection:
            await connection.execute(insert(ga_runs).values(**row))
        return _public_run(row)

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(select(ga_runs).where(ga_runs.c.id == run_id))).mappings().first()
        return _public_run(dict(row)) if row else None

    async def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        async with self.engine.connect() as connection:
            rows = (await connection.execute(select(ga_runs).order_by(ga_runs.c.created_at.desc()).limit(limit))).mappings().all()
        return [_public_run(dict(row)) for row in rows]

    async def update_run(self, run_id: str, **values: Any) -> None:
        allowed = {"status", "result", "error", "started_at", "completed_at"}
        if not values or not set(values).issubset(allowed):
            raise ValueError("invalid GA run update")
        async with self.engine.begin() as connection:
            await connection.execute(update(ga_runs).where(ga_runs.c.id == run_id).values(**values))

    async def promote(self, run_id: str, system: str, candidate: dict[str, Any], reason: str, approved_by: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        row = {"run_id": run_id, "system": system, "candidate": candidate, "reason": reason,
               "approved_by": approved_by, "approved_at": now, "status": "approved_for_export"}
        async with self.engine.begin() as connection:
            await connection.execute(insert(ga_promotions).values(**row))
        return {**row, "approved_at": now.isoformat()}


def _public_run(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in ("created_at", "started_at", "completed_at"):
        if result.get(key) is not None:
            result[key] = result[key].isoformat()
    return result


class InMemoryGAStore:
    def __init__(self):
        self.runs: dict[str, dict[str, Any]] = {}
        self.promotions: dict[tuple[str, str], dict[str, Any]] = {}

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def recover_interrupted_runs(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        interrupted = 0
        for row in self.runs.values():
            if row["status"] in {"pending", "running"}:
                row.update({
                    "status": "failed",
                    "error": "GA run interrupted by Ensemble process restart; rerun explicitly",
                    "completed_at": now,
                })
                interrupted += 1
        return interrupted

    async def create_run(self, config: dict[str, Any]) -> dict[str, Any]:
        run_id, now = str(uuid4()), datetime.now(timezone.utc).isoformat()
        row = {"id": run_id, "status": "pending", "config": config, "result": None,
               "error": None, "created_at": now, "started_at": None, "completed_at": None}
        self.runs[run_id] = row
        return dict(row)

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        return dict(self.runs[run_id]) if run_id in self.runs else None

    async def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return sorted((dict(row) for row in self.runs.values()), key=lambda row: row["created_at"], reverse=True)[:limit]

    async def update_run(self, run_id: str, **values: Any) -> None:
        if run_id not in self.runs:
            raise KeyError("GA run not found")
        self.runs[run_id].update(values)

    async def promote(self, run_id: str, system: str, candidate: dict[str, Any], reason: str, approved_by: str) -> dict[str, Any]:
        if run_id not in self.runs:
            raise KeyError("GA run not found")
        key = (run_id, system)
        if key in self.promotions:
            raise ValueError("candidate already has a promotion record")
        row = {"run_id": run_id, "system": system, "candidate": candidate, "reason": reason,
               "approved_by": approved_by, "approved_at": datetime.now(timezone.utc).isoformat(),
               "status": "approved_for_export"}
        self.promotions[key] = row
        return dict(row)
