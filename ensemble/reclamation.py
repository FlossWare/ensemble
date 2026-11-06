from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4
from typing import Any, Protocol

from sqlalchemy import Column, DateTime, Integer, JSON, MetaData, String, Table, Text, and_, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.engine import make_url

from .partitioning import PartitionedAsyncEngine
from .deletion import ReclamationJob


reclamation_metadata = MetaData()
deletion_audit = Table(
    "ensemble_deletion_audit",
    reclamation_metadata,
    Column("audit_id", String(36), primary_key=True),
    Column("source_id", String(1024), nullable=False),
    Column("event", String(64), nullable=False),
    Column("actor", String(256), nullable=True),
    Column("reason", Text, nullable=True),
    Column("details", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

reclamation_jobs = Table(
    "ensemble_source_reclamation",
    reclamation_metadata,
    Column("source_id", String(1024), primary_key=True),
    Column("state", String(16), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("last_error", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("actor", String(256), nullable=True),
    Column("reason", Text, nullable=True),
    Column("retain_until", DateTime(timezone=True), nullable=True),
)


class ReclamationStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def enqueue(self, source_id: str, *, actor: str | None = None, reason: str | None = None, requested_at: datetime | None = None, retain_until: datetime | None = None) -> ReclamationJob: ...
    async def claim_due(self, limit: int = 100) -> list[ReclamationJob]: ...
    async def complete(self, source_id: str) -> None: ...
    async def cancel(self, source_id: str) -> bool: ...
    async def cancel(self, source_id: str) -> bool:
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            row = (await connection.execute(
                select(reclamation_jobs.c.state).where(
                    reclamation_jobs.c.source_id == source_id,
                )
            )).first()
            if row is None:
                return True
            if row.state in {"running", "complete"}:
                return False
            if row.state == "cancelled":
                return True
            result = await connection.execute(
                update(reclamation_jobs).where(
                    reclamation_jobs.c.source_id == source_id,
                    reclamation_jobs.c.state.in_(["pending", "retry"]),
                ).values(state="cancelled", updated_at=now, last_error=None)
            )
            cancelled = result.rowcount == 1
        if cancelled:
            await self.record_audit(source_id, "cancelled", details={"reason": "source_restored"})
        return cancelled

    async def retry(self, source_id: str, error: str, delay_seconds: int | None = None) -> None: ...
    async def status(self) -> dict[str, int]: ...
    async def record_audit(self, source_id: str, event: str, *, actor: str | None = None, reason: str | None = None, details: dict[str, Any] | None = None) -> None: ...
    async def audit_events(self, source_id: str, limit: int = 100) -> list[dict[str, Any]]: ...


class SqliteReclamationStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.getenv(
            "ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db"
        )
        if make_url(self.database_url).get_backend_name() != "sqlite":
            raise ValueError("Ensemble reclamation persistence supports SQLite only")
        self.engine: AsyncEngine = PartitionedAsyncEngine(self.database_url, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            names = await connection.run_sync(
                lambda conn: __import__("sqlalchemy").inspect(conn).get_table_names()
            )
        if not {"ensemble_source_reclamation", "ensemble_deletion_audit"}.issubset(names):
            raise RuntimeError("reclamation schema is not initialized; run 'alembic upgrade head'")

    async def close(self) -> None:
        await self.engine.dispose()

    async def enqueue(self, source_id: str, *, actor: str | None = None, reason: str | None = None, requested_at: datetime | None = None, retain_until: datetime | None = None) -> ReclamationJob:
        if not isinstance(source_id, str) or not source_id or len(source_id) > 1024:
            raise ValueError("source_id must contain 1-1024 characters")
        now = requested_at or datetime.now(timezone.utc)
        if now.tzinfo is None or (retain_until is not None and retain_until.tzinfo is None):
            raise ValueError("deletion timestamps must be timezone-aware")
        available_at = retain_until or now
        async with self.engine.begin() as connection:
            statement = sqlite_insert(reclamation_jobs).values(
                source_id=source_id, state="pending", attempts=0,
                available_at=available_at, last_error=None, created_at=now,
                updated_at=now, completed_at=None, actor=actor, reason=reason,
                retain_until=retain_until,
            ).on_conflict_do_update(
                index_elements=[reclamation_jobs.c.source_id],
                set_={
                    "state": "pending", "available_at": available_at, "last_error": None,
                    "updated_at": now, "completed_at": None, "actor": actor,
                    "reason": reason, "retain_until": retain_until,
                },
                where=reclamation_jobs.c.state != "complete",
            )
            await connection.execute(statement)
            row = (await connection.execute(
                select(reclamation_jobs).where(reclamation_jobs.c.source_id == source_id)
            )).mappings().one()
        return dict(row)  # type: ignore[return-value]

    async def claim_due(self, limit: int = 100) -> list[ReclamationJob]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        now = datetime.now(timezone.utc)
        stale_claim = now - timedelta(minutes=5)
        eligible = or_(
            and_(
                reclamation_jobs.c.state.in_(["pending", "retry"]),
                reclamation_jobs.c.available_at <= now,
            ),
            and_(
                reclamation_jobs.c.state == "running",
                reclamation_jobs.c.updated_at <= stale_claim,
            ),
        )
        due_ids = (
            select(reclamation_jobs.c.source_id)
            .where(eligible)
            .order_by(reclamation_jobs.c.created_at, reclamation_jobs.c.source_id)
            .limit(limit)
        )
        async with self.engine.begin() as connection:
            # Claim in one conditional UPDATE. A select-then-update sequence
            # lets two workers read the same pending rows before either writes.
            # Rechecking eligibility in the UPDATE makes the claim exclusive.
            claimed = (await connection.execute(
                update(reclamation_jobs)
                .where(reclamation_jobs.c.source_id.in_(due_ids), eligible)
                .values(
                    state="running",
                    attempts=reclamation_jobs.c.attempts + 1,
                    updated_at=now,
                )
                .returning(reclamation_jobs)
            )).mappings().all()
        return [dict(row) for row in claimed]  # type: ignore[return-value]

    async def complete(self, source_id: str) -> None:
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            await connection.execute(
                update(reclamation_jobs).where(
                    reclamation_jobs.c.source_id == source_id,
                    reclamation_jobs.c.state == "running",
                ).values(state="complete", completed_at=now, updated_at=now, last_error=None)
            )

    async def retry(self, source_id: str, error: str, delay_seconds: int | None = None) -> None:
        if delay_seconds is not None and (isinstance(delay_seconds, bool) or not isinstance(delay_seconds, int) or not 0 <= delay_seconds <= 3600):
            raise ValueError("delay_seconds must be between 0 and 3600")
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            row = (await connection.execute(
                select(reclamation_jobs.c.attempts).where(
                    reclamation_jobs.c.source_id == source_id
                )
            )).first()
            attempts = int(row.attempts) if row else 1
            delay = delay_seconds if delay_seconds is not None else min(3600, 2 ** min(attempts, 10))
            await connection.execute(
                update(reclamation_jobs).where(
                    reclamation_jobs.c.source_id == source_id,
                    reclamation_jobs.c.state == "running",
                ).values(
                    state="retry", available_at=now + timedelta(seconds=delay),
                    updated_at=now, last_error=error[:4000],
                )
            )

    async def record_audit(self, source_id: str, event: str, *, actor: str | None = None, reason: str | None = None, details: dict[str, Any] | None = None) -> None:
        if not source_id or len(source_id) > 1024 or not event or len(event) > 64:
            raise ValueError("source_id and event are required and bounded")
        async with self.engine.begin() as connection:
            await connection.execute(deletion_audit.insert().values(
                audit_id=str(uuid4()), source_id=source_id, event=event,
                actor=actor, reason=reason, details=details or {},
                created_at=datetime.now(timezone.utc),
            ))

    async def audit_events(self, source_id: str, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                select(deletion_audit).where(
                    deletion_audit.c.source_id == source_id,
                ).order_by(
                    deletion_audit.c.created_at.desc(),
                    deletion_audit.c.audit_id.desc(),
                ).limit(limit)
            )).mappings().all()
        return [dict(row) for row in rows]

    async def status(self) -> dict[str, int]:
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                select(reclamation_jobs.c.state, __import__("sqlalchemy").func.count())
                .group_by(reclamation_jobs.c.state)
            )).all()
        result = {"pending": 0, "running": 0, "retry": 0, "complete": 0, "cancelled": 0}
        result.update({state: count for state, count in rows})
        return result


class InMemoryReclamationStore:
    def __init__(self):
        self._jobs: dict[str, ReclamationJob] = {}
        self._audit_events: list[dict[str, Any]] = []

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def enqueue(self, source_id: str, *, actor: str | None = None, reason: str | None = None, requested_at: datetime | None = None, retain_until: datetime | None = None) -> ReclamationJob:
        now = requested_at or datetime.now(timezone.utc)
        if now.tzinfo is None or (retain_until is not None and retain_until.tzinfo is None):
            raise ValueError("deletion timestamps must be timezone-aware")
        job = self._jobs.get(source_id)
        if job is None or job["state"] != "complete":
            self._jobs[source_id] = {
                "source_id": source_id, "state": "pending",
                "attempts": job["attempts"] if job else 0,
                "available_at": retain_until or now, "last_error": None,
                "created_at": job["created_at"] if job else now,
                "updated_at": now, "completed_at": None,
                "actor": actor, "reason": reason, "retain_until": retain_until,
            }
        return dict(self._jobs[source_id])  # type: ignore[return-value]

    async def claim_due(self, limit: int = 100) -> list[ReclamationJob]:
        now = datetime.now(timezone.utc)
        claimed: list[ReclamationJob] = []
        for job in sorted(self._jobs.values(), key=lambda item: item["created_at"]):
            if len(claimed) >= limit:
                break
            stale = job["state"] == "running" and job["updated_at"] <= now - timedelta(minutes=5)
            if job["state"] in {"pending", "retry"} and job["available_at"] <= now or stale:
                job["state"] = "running"
                job["attempts"] += 1
                job["updated_at"] = now
                claimed.append(dict(job))
        return claimed

    async def complete(self, source_id: str) -> None:
        job = self._jobs[source_id]
        job.update(state="complete", completed_at=datetime.now(timezone.utc),
                   updated_at=datetime.now(timezone.utc), last_error=None)

    async def cancel(self, source_id: str) -> bool:
        job = self._jobs.get(source_id)
        if job is None:
            return True
        if job["state"] in {"running", "complete"}:
            return False
        if job["state"] == "cancelled":
            return True
        job.update(state="cancelled", updated_at=datetime.now(timezone.utc), last_error=None)
        await self.record_audit(source_id, "cancelled", actor=job.get("actor"), reason=job.get("reason"), details={"reason": "source_restored"})
        return True

    async def retry(self, source_id: str, error: str, delay_seconds: int | None = None) -> None:
        if delay_seconds is not None and (isinstance(delay_seconds, bool) or not isinstance(delay_seconds, int) or not 0 <= delay_seconds <= 3600):
            raise ValueError("delay_seconds must be between 0 and 3600")
        job = self._jobs[source_id]
        now = datetime.now(timezone.utc)
        delay = delay_seconds if delay_seconds is not None else min(3600, 2 ** min(job["attempts"], 10))
        job.update(state="retry", available_at=now + timedelta(seconds=delay),
                   updated_at=now, last_error=error[:4000])

    async def record_audit(self, source_id: str, event: str, *, actor: str | None = None, reason: str | None = None, details: dict[str, Any] | None = None) -> None:
        if not source_id or len(source_id) > 1024 or not event or len(event) > 64:
            raise ValueError("source_id and event are required and bounded")
        self._audit_events.append({
            "audit_id": str(uuid4()), "source_id": source_id, "event": event,
            "actor": actor, "reason": reason, "details": details or {},
            "created_at": datetime.now(timezone.utc),
        })

    async def audit_events(self, source_id: str, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        return list(reversed([
            item for item in self._audit_events if item["source_id"] == source_id
        ][-limit:]))

    async def status(self) -> dict[str, int]:
        result = {"pending": 0, "running": 0, "retry": 0, "complete": 0}
        for job in self._jobs.values():
            result[job["state"]] += 1
        return result
