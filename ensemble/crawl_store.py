from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, MetaData, String, Table, Text, select, update
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.engine import make_url

from .partitioning import PartitionedAsyncEngine

CRAWL_JOB_LEASE_SECONDS = 300

metadata = MetaData()
crawl_jobs = Table(
    "ensemble_crawl_jobs", metadata,
    Column("job_id", String(32), primary_key=True),
    Column("uri", String(2048), nullable=False),
    Column("max_pages", Integer, nullable=False),
    Column("max_depth", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("result", JSON, nullable=True),
    Column("ingestion", JSON, nullable=True),
    Column("auto_ingest", Boolean, nullable=False, default=False),
    Column("schedule_id", String(32), nullable=True),
    Column("last_error", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
)


def _record(row: Any) -> dict[str, Any]:
    return {
        "job_id": row["job_id"], "uri": row["uri"],
        "max_pages": row["max_pages"], "max_depth": row["max_depth"],
        "status": row["status"], "attempts": row["attempts"],
        "result": row["result"], "ingestion": row["ingestion"],
        "auto_ingest": row["auto_ingest"], "schedule_id": row["schedule_id"],
        "last_error": row["last_error"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
    }


class CrawlJobStore(Protocol):
    async def initialize(self) -> None: ...
    async def check_ready(self) -> None: ...
    async def close(self) -> None: ...
    async def enqueue(self, uri: str, max_pages: int, max_depth: int, *, auto_ingest: bool = False, schedule_id: str | None = None) -> dict[str, Any]: ...
    async def claim_next(self) -> dict[str, Any] | None: ...
    async def heartbeat(self, job_id: str) -> bool: ...
    async def complete(self, job_id: str, result: dict[str, Any]) -> dict[str, Any]: ...
    async def update_ingestion(self, job_id: str, ingestion: dict[str, Any]) -> dict[str, Any]: ...
    async def fail(self, job_id: str, error: str) -> None: ...
    async def get(self, job_id: str) -> dict[str, Any] | None: ...
    async def list(self, limit: int = 50) -> list[dict[str, Any]]: ...
    async def retry(self, job_id: str) -> dict[str, Any]: ...
    async def recover_interrupted(self) -> int: ...


class SqliteCrawlJobStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.getenv(
            "ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db"
        )
        if make_url(self.database_url).get_backend_name() != "sqlite":
            raise ValueError("Ensemble crawl jobs support SQLite only")
        self.engine: AsyncEngine = PartitionedAsyncEngine(self.database_url, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            names = await connection.run_sync(lambda conn: __import__("sqlalchemy").inspect(conn).get_table_names())
        if "ensemble_crawl_jobs" not in names:
            raise RuntimeError("crawl job schema is not initialized; run 'alembic upgrade head'")

    async def check_ready(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(select(crawl_jobs.c.job_id).limit(1))

    async def close(self) -> None:
        await self.engine.dispose()

    async def enqueue(self, uri: str, max_pages: int, max_depth: int, *, auto_ingest: bool = False, schedule_id: str | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        job_id = uuid.uuid4().hex
        async with self.engine.begin() as connection:
            await connection.execute(crawl_jobs.insert().values(
                job_id=job_id, uri=uri, max_pages=max_pages, max_depth=max_depth,
                status="pending", attempts=0, result=None, ingestion=None,
                auto_ingest=auto_ingest, schedule_id=schedule_id, last_error=None,
                created_at=now, updated_at=now, completed_at=None,
            ))
        return await self.get(job_id)

    async def claim_next(self) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            row = (await connection.execute(
                select(crawl_jobs).where(crawl_jobs.c.status == "pending")
                .order_by(crawl_jobs.c.created_at.asc()).limit(1)
            )).mappings().first()
            if row is None:
                return None
            changed = await connection.execute(update(crawl_jobs).where(
                crawl_jobs.c.job_id == row["job_id"],
                crawl_jobs.c.status == "pending",
            ).values(status="running", attempts=crawl_jobs.c.attempts + 1, updated_at=now))
            if changed.rowcount != 1:
                return None
            claimed = (await connection.execute(select(crawl_jobs).where(
                crawl_jobs.c.job_id == row["job_id"],
            ))).mappings().first()
        return _record(claimed)

    async def heartbeat(self, job_id: str) -> bool:
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            changed = await connection.execute(update(crawl_jobs).where(
                crawl_jobs.c.job_id == job_id,
                crawl_jobs.c.status.in_({"running", "completed"}),
            ).values(updated_at=now))
        return changed.rowcount == 1

    async def complete(self, job_id: str, result: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            changed = await connection.execute(update(crawl_jobs).where(
                crawl_jobs.c.job_id == job_id, crawl_jobs.c.status == "running",
            ).values(status="completed", result=result, last_error=None, updated_at=now, completed_at=now))
            if changed.rowcount != 1:
                raise ValueError("running crawl job not found")
            row = (await connection.execute(select(crawl_jobs).where(crawl_jobs.c.job_id == job_id))).mappings().first()
        return _record(row)

    async def update_ingestion(self, job_id: str, ingestion: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            changed = await connection.execute(update(crawl_jobs).where(
                crawl_jobs.c.job_id == job_id, crawl_jobs.c.status == "completed",
            ).values(ingestion=ingestion, updated_at=now))
            if changed.rowcount != 1:
                raise ValueError("completed crawl job not found")
            row = (await connection.execute(select(crawl_jobs).where(crawl_jobs.c.job_id == job_id))).mappings().first()
        return _record(row)

    async def fail(self, job_id: str, error: str) -> None:
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            await connection.execute(update(crawl_jobs).where(
                crawl_jobs.c.job_id == job_id, crawl_jobs.c.status == "running",
            ).values(status="failed", last_error=str(error)[:2000], updated_at=now))

    async def get(self, job_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(select(crawl_jobs).where(crawl_jobs.c.job_id == job_id))).mappings().first()
        return _record(row) if row else None

    async def list(self, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                select(crawl_jobs).order_by(crawl_jobs.c.updated_at.desc()).limit(limit)
            )).mappings().all()
        return [_record(row) for row in rows]

    async def retry(self, job_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            changed = await connection.execute(update(crawl_jobs).where(
                crawl_jobs.c.job_id == job_id,
                crawl_jobs.c.status.in_({"failed", "interrupted"}),
            ).values(status="pending", last_error=None, result=None, completed_at=None, updated_at=now))
            if changed.rowcount != 1:
                row = (await connection.execute(select(crawl_jobs).where(crawl_jobs.c.job_id == job_id))).mappings().first()
                if row is None:
                    raise KeyError("crawl job not found")
                raise ValueError(f"crawl job cannot be retried from status {row['status']}")
        return await self.get(job_id)

    async def recover_interrupted(self) -> int:
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(seconds=CRAWL_JOB_LEASE_SECONDS)
        async with self.engine.begin() as connection:
            changed = await connection.execute(update(crawl_jobs).where(
                crawl_jobs.c.status == "running",
                crawl_jobs.c.updated_at <= stale_before,
            ).values(
                status="interrupted",
                last_error="Process restarted before the crawl completed; retry this job to resume.",
                updated_at=now,
            ))
            recovered = int(changed.rowcount or 0)
            rows = (await connection.execute(select(crawl_jobs).where(
                crawl_jobs.c.status == "completed",
                crawl_jobs.c.ingestion.is_not(None),
                crawl_jobs.c.updated_at <= stale_before,
            ))).mappings().all()
            for row in rows:
                ingestion = row["ingestion"]
                if not isinstance(ingestion, dict) or ingestion.get("status") != "running":
                    continue
                for page in ingestion.get("pages", []):
                    if isinstance(page, dict) and page.get("status") == "running":
                        page.update(
                            status="interrupted",
                            last_error="Process restarted before this page completed; resubmit ingestion to retry.",
                        )
                ingestion["status"] = "partial"
                await connection.execute(update(crawl_jobs).where(
                    crawl_jobs.c.job_id == row["job_id"],
                ).values(ingestion=ingestion, updated_at=now))
                recovered += 1
        return recovered


class InMemoryCrawlJobStore:
    def __init__(self):
        self._jobs: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None: return None
    async def check_ready(self) -> None: return None
    async def close(self) -> None: return None

    async def enqueue(self, uri: str, max_pages: int, max_depth: int, *, auto_ingest: bool = False, schedule_id: str | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        job_id = uuid.uuid4().hex
        self._jobs[job_id] = {
            "job_id": job_id, "uri": uri, "max_pages": max_pages, "max_depth": max_depth,
            "status": "pending", "attempts": 0, "result": None, "ingestion": None,
            "auto_ingest": auto_ingest, "schedule_id": schedule_id, "last_error": None,
            "created_at": now, "updated_at": now, "completed_at": None,
        }
        return dict(self._jobs[job_id])

    async def claim_next(self) -> dict[str, Any] | None:
        pending = sorted((j for j in self._jobs.values() if j["status"] == "pending"), key=lambda j: j["created_at"])
        if not pending: return None
        row = pending[0]
        row.update(status="running", attempts=row["attempts"] + 1, updated_at=datetime.now(timezone.utc).isoformat())
        return dict(row)

    async def heartbeat(self, job_id: str) -> bool:
        row = self._jobs.get(job_id)
        if row is None or row["status"] not in {"running", "completed"}:
            return False
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        return True

    async def complete(self, job_id: str, result: dict[str, Any]) -> dict[str, Any]:
        row = self._jobs.get(job_id)
        if row is None or row["status"] != "running": raise ValueError("running crawl job not found")
        now = datetime.now(timezone.utc).isoformat()
        row.update(status="completed", result=result, last_error=None, updated_at=now, completed_at=now)
        return dict(row)

    async def update_ingestion(self, job_id: str, ingestion: dict[str, Any]) -> dict[str, Any]:
        row = self._jobs.get(job_id)
        if row is None or row["status"] != "completed":
            raise ValueError("completed crawl job not found")
        row.update(ingestion=ingestion, updated_at=datetime.now(timezone.utc).isoformat())
        return dict(row)

    async def fail(self, job_id: str, error: str) -> None:
        row = self._jobs.get(job_id)
        if row and row["status"] == "running":
            row.update(status="failed", last_error=str(error)[:2000], updated_at=datetime.now(timezone.utc).isoformat())

    async def get(self, job_id: str) -> dict[str, Any] | None:
        row = self._jobs.get(job_id)
        return dict(row) if row else None

    async def list(self, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        return sorted((dict(j) for j in self._jobs.values()), key=lambda j: j["updated_at"], reverse=True)[:limit]

    async def retry(self, job_id: str) -> dict[str, Any]:
        row = self._jobs.get(job_id)
        if row is None: raise KeyError("crawl job not found")
        if row["status"] not in {"failed", "interrupted"}: raise ValueError(f"crawl job cannot be retried from status {row['status']}")
        row.update(status="pending", last_error=None, result=None, completed_at=None, updated_at=datetime.now(timezone.utc).isoformat())
        return dict(row)

    async def recover_interrupted(self) -> int:
        recovered = 0
        now_dt = datetime.now(timezone.utc)
        stale_before = now_dt - timedelta(seconds=CRAWL_JOB_LEASE_SECONDS)
        now = now_dt.isoformat()
        for row in self._jobs.values():
            updated = datetime.fromisoformat(row["updated_at"])
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            stale = updated <= stale_before
            if row["status"] == "running" and stale:
                row.update(status="interrupted", last_error="Process restarted before the crawl completed; retry this job to resume.", updated_at=now)
                recovered += 1
            ingestion = row.get("ingestion")
            if stale and row["status"] == "completed" and isinstance(ingestion, dict) and ingestion.get("status") == "running":
                for page in ingestion.get("pages", []):
                    if isinstance(page, dict) and page.get("status") == "running":
                        page.update(
                            status="interrupted",
                            last_error="Process restarted before this page completed; resubmit ingestion to retry.",
                        )
                ingestion["status"] = "partial"
                row.update(ingestion=ingestion, updated_at=now)
                recovered += 1
        return recovered
