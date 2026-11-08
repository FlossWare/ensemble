from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.engine import make_url

from .crawl_store import crawl_jobs, CrawlJobStore
from .partitioning import PartitionedAsyncEngine

metadata = MetaData()
schedules = Table(
    "ensemble_source_schedules", metadata,
    Column("schedule_id", String(32), primary_key=True),
    Column("uri", String(2048), nullable=False),
    Column("interval_seconds", Integer, nullable=False),
    Column("max_pages", Integer, nullable=False),
    Column("max_depth", Integer, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("next_run_at", DateTime(timezone=True), nullable=False),
    Column("last_run_at", DateTime(timezone=True), nullable=True),
    Column("last_job_id", String(32), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def _record(row: Any) -> dict[str, Any]:
    return {
        "schedule_id": row["schedule_id"], "uri": row["uri"],
        "interval_seconds": row["interval_seconds"], "max_pages": row["max_pages"],
        "max_depth": row["max_depth"], "enabled": bool(row["enabled"]),
        "next_run_at": row["next_run_at"].isoformat(),
        "last_run_at": row["last_run_at"].isoformat() if row["last_run_at"] else None,
        "last_job_id": row["last_job_id"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def _validate_schedule(uri: str, interval_seconds: int, max_pages: int, max_depth: int) -> None:
    if not isinstance(uri, str) or not uri or len(uri) > 2048:
        raise ValueError("uri must be a non-empty URL of at most 2048 characters")
    if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int) or not 3600 <= interval_seconds <= 2_592_000:
        raise ValueError("interval_seconds must be between 3600 and 2592000")
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= 20:
        raise ValueError("max_pages must be between 1 and 20")
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 0 <= max_depth <= 3:
        raise ValueError("max_depth must be between 0 and 3")


class SourceScheduleStore(Protocol):
    async def initialize(self) -> None: ...
    async def check_ready(self) -> None: ...
    async def close(self) -> None: ...
    async def create(self, uri: str, interval_seconds: int, max_pages: int, max_depth: int, run_immediately: bool = True) -> dict[str, Any]: ...
    async def list(self, limit: int = 50) -> list[dict[str, Any]]: ...
    async def get(self, schedule_id: str) -> dict[str, Any] | None: ...
    async def set_enabled(self, schedule_id: str, enabled: bool) -> dict[str, Any]: ...
    async def delete(self, schedule_id: str) -> bool: ...
    async def dispatch_due(self, limit: int = 10) -> int: ...


class SqliteSourceScheduleStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.getenv(
            "ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db"
        )
        if make_url(self.database_url).get_backend_name() != "sqlite":
            raise ValueError("Ensemble source schedules support SQLite only")
        self.engine: AsyncEngine = PartitionedAsyncEngine(self.database_url, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            names = await connection.run_sync(lambda conn: __import__("sqlalchemy").inspect(conn).get_table_names())
        if "ensemble_source_schedules" not in names:
            raise RuntimeError("source schedule schema is not initialized; run 'alembic upgrade head'")

    async def check_ready(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(select(schedules.c.schedule_id).limit(1))

    async def close(self) -> None:
        await self.engine.dispose()

    async def create(self, uri: str, interval_seconds: int, max_pages: int, max_depth: int, run_immediately: bool = True) -> dict[str, Any]:
        _validate_schedule(uri, interval_seconds, max_pages, max_depth)
        now = datetime.now(timezone.utc)
        schedule_id = uuid.uuid4().hex
        async with self.engine.begin() as connection:
            await connection.execute(schedules.insert().values(
                schedule_id=schedule_id, uri=uri, interval_seconds=interval_seconds,
                max_pages=max_pages, max_depth=max_depth, enabled=True,
                next_run_at=now if run_immediately else now + timedelta(seconds=interval_seconds),
                last_run_at=None, last_job_id=None, created_at=now, updated_at=now,
            ))
        return await self.get(schedule_id)

    async def list(self, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                select(schedules).order_by(schedules.c.updated_at.desc()).limit(limit)
            )).mappings().all()
        return [_record(row) for row in rows]

    async def get(self, schedule_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(select(schedules).where(
                schedules.c.schedule_id == schedule_id,
            ))).mappings().first()
        return _record(row) if row else None

    async def set_enabled(self, schedule_id: str, enabled: bool) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        values: dict[str, Any] = {"enabled": enabled, "updated_at": now}
        if enabled:
            values["next_run_at"] = now
        async with self.engine.begin() as connection:
            changed = await connection.execute(update(schedules).where(
                schedules.c.schedule_id == schedule_id,
            ).values(**values))
            if changed.rowcount != 1:
                raise KeyError("source schedule not found")
        return await self.get(schedule_id)

    async def delete(self, schedule_id: str) -> bool:
        async with self.engine.begin() as connection:
            changed = await connection.execute(delete(schedules).where(
                schedules.c.schedule_id == schedule_id,
            ))
        return bool(changed.rowcount)

    async def dispatch_due(self, limit: int = 10) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        now = datetime.now(timezone.utc)
        dispatched = 0
        async with self.engine.begin() as connection:
            rows = (await connection.execute(
                select(schedules).where(
                    schedules.c.enabled.is_(True), schedules.c.next_run_at <= now,
                ).order_by(schedules.c.next_run_at.asc()).limit(limit)
            )).mappings().all()
            for row in rows:
                active_job = (await connection.execute(
                    select(crawl_jobs.c.job_id).where(
                        crawl_jobs.c.schedule_id == row["schedule_id"],
                        crawl_jobs.c.status.in_({"pending", "running"}),
                    ).limit(1)
                )).first()
                if active_job is not None:
                    # Keep the schedule due. It will dispatch promptly after
                    # the previous scheduled crawl finishes.
                    continue
                job_id = uuid.uuid4().hex
                next_run = now + timedelta(seconds=row["interval_seconds"])
                changed = await connection.execute(update(schedules).where(
                    schedules.c.schedule_id == row["schedule_id"],
                    schedules.c.enabled.is_(True),
                    schedules.c.next_run_at <= now,
                ).values(
                    next_run_at=next_run, last_run_at=now,
                    last_job_id=job_id, updated_at=now,
                ))
                if changed.rowcount != 1:
                    continue
                await connection.execute(crawl_jobs.insert().values(
                    job_id=job_id, uri=row["uri"], max_pages=row["max_pages"],
                    max_depth=row["max_depth"], status="pending", attempts=0,
                    result=None, ingestion=None, auto_ingest=True,
                    schedule_id=row["schedule_id"], last_error=None,
                    created_at=now, updated_at=now, completed_at=None,
                ))
                dispatched += 1
        return dispatched


class InMemorySourceScheduleStore:
    def __init__(self, crawl_store: CrawlJobStore):
        self.crawl_store = crawl_store
        self._items: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None: return None
    async def check_ready(self) -> None: return None
    async def close(self) -> None: return None

    async def create(self, uri: str, interval_seconds: int, max_pages: int, max_depth: int, run_immediately: bool = True) -> dict[str, Any]:
        _validate_schedule(uri, interval_seconds, max_pages, max_depth)
        now = datetime.now(timezone.utc)
        schedule_id = uuid.uuid4().hex
        next_run = now if run_immediately else now + timedelta(seconds=interval_seconds)
        item = {
            "schedule_id": schedule_id, "uri": uri, "interval_seconds": interval_seconds,
            "max_pages": max_pages, "max_depth": max_depth, "enabled": True,
            "next_run_at": next_run.isoformat(), "last_run_at": None, "last_job_id": None,
            "created_at": now.isoformat(), "updated_at": now.isoformat(),
        }
        self._items[schedule_id] = item
        return dict(item)

    async def list(self, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        return sorted((dict(item) for item in self._items.values()), key=lambda item: item["updated_at"], reverse=True)[:limit]

    async def get(self, schedule_id: str) -> dict[str, Any] | None:
        item = self._items.get(schedule_id)
        return dict(item) if item else None

    async def set_enabled(self, schedule_id: str, enabled: bool) -> dict[str, Any]:
        item = self._items.get(schedule_id)
        if item is None: raise KeyError("source schedule not found")
        now = datetime.now(timezone.utc)
        item["enabled"] = enabled
        item["updated_at"] = now.isoformat()
        if enabled:
            item["next_run_at"] = now.isoformat()
        return dict(item)

    async def delete(self, schedule_id: str) -> bool:
        return self._items.pop(schedule_id, None) is not None

    async def dispatch_due(self, limit: int = 10) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        now = datetime.now(timezone.utc)
        due = sorted(
            (item for item in self._items.values()
             if item["enabled"] and datetime.fromisoformat(item["next_run_at"]) <= now),
            key=lambda item: item["next_run_at"],
        )[:limit]
        dispatched = 0
        for item in due:
            previous = (
                await self.crawl_store.get(item["last_job_id"])
                if item.get("last_job_id") else None
            )
            if previous and previous.get("status") in {"pending", "running"}:
                # Leave next_run_at due so dispatch resumes after the active
                # scheduled crawl finishes.
                continue
            job = await self.crawl_store.enqueue(
                item["uri"], item["max_pages"], item["max_depth"],
                auto_ingest=True, schedule_id=item["schedule_id"],
            )
            item.update(
                last_run_at=now.isoformat(), last_job_id=job["job_id"],
                next_run_at=(now + timedelta(seconds=item["interval_seconds"])).isoformat(),
                updated_at=now.isoformat(),
            )
            dispatched += 1
        return dispatched
