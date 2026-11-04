from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import Column, DateTime, Float, JSON, MetaData, String, Table, delete, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from .partitioning import PartitionedAsyncEngine

MAX_TRACE_RECORDS = 10_000

metadata = MetaData()
traces = Table(
    "ensemble_execution_traces", metadata,
    Column("trace_id", String(32), primary_key=True),
    Column("request_id", String(256), nullable=False),
    Column("operation", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("duration_ms", Float, nullable=True),
    Column("attributes", JSON, nullable=False),
    Column("stages", JSON, nullable=False),
    Column("error_category", String(128), nullable=True),
)


class ExecutionTraceStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def create(self, record: dict[str, Any]) -> None: ...
    async def record_stage(self, trace_id: str, stage: dict[str, Any]) -> None: ...
    async def finish(self, trace_id: str, status: str, duration_ms: float, error_category: str | None = None) -> None: ...
    async def get(self, trace_id: str) -> dict[str, Any] | None: ...
    async def list(self, limit: int = 50) -> list[dict[str, Any]]: ...


def _serialize(row: Any) -> dict[str, Any]:
    return {
        "trace_id": row["trace_id"], "request_id": row["request_id"],
        "operation": row["operation"], "status": row["status"],
        "started_at": row["started_at"].isoformat(),
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        "duration_ms": row["duration_ms"], "attributes": row["attributes"] or {},
        "stages": row["stages"] or [], "error_category": row["error_category"],
    }


class SqliteExecutionTraceStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.getenv(
            "ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db"
        )
        if make_url(self.database_url).get_backend_name() != "sqlite":
            raise ValueError("Ensemble execution traces support SQLite only")
        self.engine: AsyncEngine = PartitionedAsyncEngine(self.database_url, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            names = await connection.run_sync(lambda conn: __import__("sqlalchemy").inspect(conn).get_table_names())
        if "ensemble_execution_traces" not in names:
            raise RuntimeError("execution trace schema is not initialized; run 'alembic upgrade head'")

    async def close(self) -> None:
        await self.engine.dispose()

    async def create(self, record: dict[str, Any]) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(traces.insert().values(**record))
            overflow = select(traces.c.trace_id).order_by(
                traces.c.started_at.desc()
            ).offset(MAX_TRACE_RECORDS)
            await connection.execute(delete(traces).where(traces.c.trace_id.in_(overflow)))

    async def record_stage(self, trace_id: str, stage: dict[str, Any]) -> None:
        async with self.engine.begin() as connection:
            row = (await connection.execute(
                select(traces.c.stages).where(traces.c.trace_id == trace_id)
            )).first()
            if row is None:
                return
            stages = list(row[0] or [])
            stages.append(stage)
            await connection.execute(update(traces).where(
                traces.c.trace_id == trace_id,
            ).values(stages=stages))

    async def finish(self, trace_id: str, status: str, duration_ms: float, error_category: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            await connection.execute(update(traces).where(
                traces.c.trace_id == trace_id, traces.c.status == "running",
            ).values(status=status, completed_at=now, duration_ms=duration_ms, error_category=error_category))

    async def get(self, trace_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(select(traces).where(
                traces.c.trace_id == trace_id,
            ))).mappings().first()
        return _serialize(row) if row else None

    async def list(self, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                select(traces).order_by(traces.c.started_at.desc()).limit(limit)
            )).mappings().all()
        return [_serialize(row) for row in rows]


class InMemoryExecutionTraceStore:
    def __init__(self):
        self._items: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None: return None
    async def close(self) -> None: return None

    async def create(self, record: dict[str, Any]) -> None:
        item = dict(record)
        item["stages"] = list(record.get("stages", []))
        self._items[record["trace_id"]] = item
        if len(self._items) > MAX_TRACE_RECORDS:
            oldest = sorted(
                self._items.values(), key=lambda value: value["started_at"]
            )[:len(self._items) - MAX_TRACE_RECORDS]
            for expired in oldest:
                self._items.pop(expired["trace_id"], None)

    async def record_stage(self, trace_id: str, stage: dict[str, Any]) -> None:
        if trace_id in self._items:
            self._items[trace_id]["stages"].append(dict(stage))

    async def finish(self, trace_id: str, status: str, duration_ms: float, error_category: str | None = None) -> None:
        item = self._items.get(trace_id)
        if item is None or item["status"] != "running":
            return
        item.update(
            status=status, completed_at=datetime.now(timezone.utc),
            duration_ms=duration_ms, error_category=error_category,
        )

    async def get(self, trace_id: str) -> dict[str, Any] | None:
        item = self._items.get(trace_id)
        if item is None:
            return None
        return {
            **item, "started_at": item["started_at"].isoformat(),
            "completed_at": item["completed_at"].isoformat() if item["completed_at"] else None,
            "attributes": dict(item["attributes"]),
            "stages": [dict(stage) for stage in item["stages"]],
        }

    async def list(self, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        items = sorted(self._items.values(), key=lambda item: item["started_at"], reverse=True)[:limit]
        return [(await self.get(item["trace_id"])) for item in items]
