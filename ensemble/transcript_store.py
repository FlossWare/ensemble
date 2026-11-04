from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import (
    Column, DateTime, Integer, JSON, MetaData, String, Table, Text, select, update,
)
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.engine import make_url

from .partitioning import PartitionedAsyncEngine

transcript_metadata = MetaData()
checkpoints = Table(
    "ensemble_transcript_checkpoints", transcript_metadata,
    Column("source_id", String(512), primary_key=True),
    Column("revision_id", String(256), primary_key=True),
    Column("project", String(256), nullable=False),
    Column("file", String(4096), nullable=False),
    Column("byte_offset", Integer, nullable=False),
    Column("last_start_offset", Integer, nullable=False),
    Column("last_payload_sha256", String(64), nullable=False),
    Column("last_result", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
jobs = Table(
    "ensemble_transcript_jobs", transcript_metadata,
    Column("job_id", String(64), primary_key=True),
    Column("source_id", String(512), nullable=False),
    Column("revision_id", String(256), nullable=False),
    Column("project", String(256), nullable=False),
    Column("file", String(4096), nullable=False),
    Column("start_offset", Integer, nullable=False),
    Column("end_offset", Integer, nullable=True),
    Column("payload_sha256", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("last_error", Text, nullable=True),
    Column("result", JSON, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
)


class TranscriptCheckpointStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def get(self, source_id: str, revision_id: str) -> dict[str, Any] | None: ...
    async def commit(
        self, source_id: str, revision_id: str, project: str, file: str,
        start_offset: int, end_offset: int, payload_sha256: str,
        result: dict[str, Any],
    ) -> dict[str, Any]: ...
    async def start_job(
        self, job_id: str, source_id: str, revision_id: str, project: str,
        file: str, start_offset: int, payload_sha256: str,
    ) -> dict[str, Any]: ...
    async def complete_job(self, job_id: str, end_offset: int, result: dict[str, Any]) -> dict[str, Any]: ...
    async def fail_job(self, job_id: str, error: str) -> None: ...
    async def get_job(self, job_id: str) -> dict[str, Any] | None: ...
    async def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]: ...
    async def recover_interrupted(self) -> int: ...


def _validate_commit(
    source_id: str, revision_id: str, project: str, file: str,
    start_offset: int, end_offset: int, payload_sha256: str,
    result: dict[str, Any],
) -> None:
    if not isinstance(source_id, str) or not source_id or len(source_id) > 512:
        raise ValueError("source_id must be a non-empty string of at most 512 characters")
    if not isinstance(revision_id, str) or not revision_id or len(revision_id) > 256:
        raise ValueError("revision_id must be a non-empty string of at most 256 characters")
    if not isinstance(project, str) or not project or len(project) > 256:
        raise ValueError("project must be a non-empty string of at most 256 characters")
    if not isinstance(file, str) or not file or len(file) > 4096:
        raise ValueError("file must be a non-empty string of at most 4096 characters")
    for name, value in (("start_offset", start_offset), ("end_offset", end_offset)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if end_offset < start_offset:
        raise ValueError("end_offset cannot be less than start_offset")
    if not isinstance(payload_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", payload_sha256):
        raise ValueError("payload_sha256 must be a lowercase SHA-256 digest")
    if not isinstance(result, dict):
        raise ValueError("result must be an object")


def _record(row: Any) -> dict[str, Any]:
    return {
        "source_id": row["source_id"], "revision_id": row["revision_id"],
        "project": row["project"], "file": row["file"],
        "byte_offset": row["byte_offset"], "last_start_offset": row["last_start_offset"],
        "last_payload_sha256": row["last_payload_sha256"], "last_result": row["last_result"],
        "updated_at": row["updated_at"].isoformat(),
    }


def _job_record(row: Any) -> dict[str, Any]:
    return {
        "job_id": row["job_id"], "source_id": row["source_id"],
        "revision_id": row["revision_id"], "project": row["project"],
        "file": row["file"], "start_offset": row["start_offset"],
        "end_offset": row["end_offset"], "payload_sha256": row["payload_sha256"],
        "status": row["status"], "attempts": row["attempts"],
        "last_error": row["last_error"], "result": row["result"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
    }


def _replay(row: dict[str, Any], start_offset: int, payload_sha256: str) -> bool:
    return (
        row["last_start_offset"] == start_offset
        and row["last_payload_sha256"] == payload_sha256
    )


def _checkpoint_mismatch(expected: int) -> ValueError:
    return ValueError(f"transcript checkpoint mismatch; expected byte offset {expected}")


class SqliteTranscriptCheckpointStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.getenv(
            "ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db"
        )
        if make_url(self.database_url).get_backend_name() != "sqlite":
            raise ValueError("Ensemble transcript checkpoints support SQLite only")
        self.engine: AsyncEngine = PartitionedAsyncEngine(self.database_url, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            names = await connection.run_sync(
                lambda conn: __import__("sqlalchemy").inspect(conn).get_table_names()
            )
        required = {"ensemble_transcript_checkpoints", "ensemble_transcript_jobs"}
        missing = required - set(names)
        if missing:
            raise RuntimeError(
                "transcript schema is not initialized; run 'alembic upgrade head' "
                f"(missing: {', '.join(sorted(missing))})"
            )

    async def close(self) -> None:
        await self.engine.dispose()

    async def get(self, source_id: str, revision_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(select(checkpoints).where(
                checkpoints.c.source_id == source_id,
                checkpoints.c.revision_id == revision_id,
            ))).mappings().first()
        return _record(row) if row else None

    async def commit(
        self, source_id: str, revision_id: str, project: str, file: str,
        start_offset: int, end_offset: int, payload_sha256: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        _validate_commit(
            source_id, revision_id, project, file, start_offset, end_offset,
            payload_sha256, result,
        )
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            row = (await connection.execute(select(checkpoints).where(
                checkpoints.c.source_id == source_id,
                checkpoints.c.revision_id == revision_id,
            ))).mappings().first()
            if row is None:
                await connection.execute(checkpoints.insert().values(
                    source_id=source_id, revision_id=revision_id, project=project, file=file,
                    byte_offset=end_offset, last_start_offset=start_offset,
                    last_payload_sha256=payload_sha256, last_result=result, updated_at=now,
                ))
            else:
                current = dict(row)
                if _replay(current, start_offset, payload_sha256):
                    return {
                        "replayed": True, "checkpoint": _record(row),
                        "result": row["last_result"],
                    }
                if row["byte_offset"] != start_offset:
                    raise _checkpoint_mismatch(row["byte_offset"])
                changed = await connection.execute(update(checkpoints).where(
                    checkpoints.c.source_id == source_id,
                    checkpoints.c.revision_id == revision_id,
                    checkpoints.c.byte_offset == start_offset,
                ).values(
                    project=project, file=file, byte_offset=end_offset,
                    last_start_offset=start_offset, last_payload_sha256=payload_sha256,
                    last_result=result, updated_at=now,
                ))
                if changed.rowcount != 1:
                    latest = (await connection.execute(select(checkpoints).where(
                        checkpoints.c.source_id == source_id,
                        checkpoints.c.revision_id == revision_id,
                    ))).mappings().first()
                    if latest is None or not _replay(dict(latest), start_offset, payload_sha256):
                        raise _checkpoint_mismatch(latest["byte_offset"] if latest else 0)
                    return {
                        "replayed": True, "checkpoint": _record(latest),
                        "result": latest["last_result"],
                    }
        return {
            "replayed": False, "checkpoint": await self.get(source_id, revision_id),
            "result": result,
        }

    async def start_job(
        self, job_id: str, source_id: str, revision_id: str, project: str,
        file: str, start_offset: int, payload_sha256: str,
    ) -> dict[str, Any]:
        _validate_commit(
            source_id, revision_id, project, file, start_offset, start_offset,
            payload_sha256, {},
        )
        if not re.fullmatch(r"[0-9a-f]{64}", job_id or ""):
            raise ValueError("job_id must be a lowercase SHA-256 digest")
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            row = (await connection.execute(select(jobs).where(
                jobs.c.job_id == job_id,
            ))).mappings().first()
            if row is None:
                await connection.execute(jobs.insert().values(
                    job_id=job_id, source_id=source_id, revision_id=revision_id,
                    project=project, file=file, start_offset=start_offset,
                    end_offset=None, payload_sha256=payload_sha256, status="running",
                    attempts=1, last_error=None, result=None,
                    created_at=now, updated_at=now, completed_at=None,
                ))
                return {"status": "running", "attempts": 1, "created": True}
            if (
                row["source_id"] != source_id or row["revision_id"] != revision_id
                or row["start_offset"] != start_offset
                or row["payload_sha256"] != payload_sha256
            ):
                raise ValueError("transcript job identity conflicts with an existing operation")
            if row["status"] in {"completed", "running"}:
                return {"status": row["status"], "attempts": row["attempts"], "created": False}
            attempts = row["attempts"] + 1
            await connection.execute(update(jobs).where(
                jobs.c.job_id == job_id,
            ).values(
                status="running", attempts=attempts, last_error=None,
                updated_at=now, completed_at=None,
            ))
        return {"status": "running", "attempts": attempts, "created": True}

    async def complete_job(self, job_id: str, end_offset: int, result: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            changed = await connection.execute(update(jobs).where(
                jobs.c.job_id == job_id,
            ).values(
                status="completed", end_offset=end_offset, result=result,
                last_error=None, updated_at=now, completed_at=now,
            ))
            if changed.rowcount != 1:
                raise ValueError("transcript job not found")
            row = (await connection.execute(select(jobs).where(jobs.c.job_id == job_id))).mappings().first()
        return _job_record(row)

    async def fail_job(self, job_id: str, error: str) -> None:
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            await connection.execute(update(jobs).where(
                jobs.c.job_id == job_id,
                jobs.c.status == "running",
            ).values(
                status="failed", last_error=str(error)[:2000],
                updated_at=now,
            ))

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(select(jobs).where(
                jobs.c.job_id == job_id,
            ))).mappings().first()
        return _job_record(row) if row else None

    async def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                select(jobs).order_by(jobs.c.updated_at.desc()).limit(limit)
            )).mappings().all()
        return [_job_record(row) for row in rows]

    async def recover_interrupted(self) -> int:
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            result = await connection.execute(update(jobs).where(
                jobs.c.status == "running",
            ).values(
                status="interrupted",
                last_error="Process restarted before the operation completed; resubmit the same slice to retry.",
                updated_at=now,
            ))
        return int(result.rowcount or 0)


class InMemoryTranscriptCheckpointStore:
    def __init__(self):
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def get(self, source_id: str, revision_id: str) -> dict[str, Any] | None:
        item = self._items.get((source_id, revision_id))
        return dict(item) if item else None

    async def commit(
        self, source_id: str, revision_id: str, project: str, file: str,
        start_offset: int, end_offset: int, payload_sha256: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        _validate_commit(
            source_id, revision_id, project, file, start_offset, end_offset,
            payload_sha256, result,
        )
        key = (source_id, revision_id)
        row = self._items.get(key)
        if row is not None:
            if _replay(row, start_offset, payload_sha256):
                return {
                    "replayed": True, "checkpoint": dict(row),
                    "result": row["last_result"],
                }
            if row["byte_offset"] != start_offset:
                raise _checkpoint_mismatch(row["byte_offset"])
        item = {
            "source_id": source_id, "revision_id": revision_id, "project": project, "file": file,
            "byte_offset": end_offset, "last_start_offset": start_offset,
            "last_payload_sha256": payload_sha256, "last_result": result,
        }
        self._items[key] = item
        return {"replayed": False, "checkpoint": dict(item), "result": result}

    async def start_job(
        self, job_id: str, source_id: str, revision_id: str, project: str,
        file: str, start_offset: int, payload_sha256: str,
    ) -> dict[str, Any]:
        _validate_commit(
            source_id, revision_id, project, file, start_offset, start_offset,
            payload_sha256, {},
        )
        if not re.fullmatch(r"[0-9a-f]{64}", job_id or ""):
            raise ValueError("job_id must be a lowercase SHA-256 digest")
        row = self._jobs.get(job_id)
        if row is not None:
            if (
                row["source_id"] != source_id or row["revision_id"] != revision_id
                or row["start_offset"] != start_offset
                or row["payload_sha256"] != payload_sha256
            ):
                raise ValueError("transcript job identity conflicts with an existing operation")
            if row["status"] in {"completed", "running"}:
                return {"status": row["status"], "attempts": row["attempts"], "created": False}
            row.update({
                "status": "running", "attempts": row["attempts"] + 1,
                "last_error": None, "updated_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": None,
            })
            return {"status": "running", "attempts": row["attempts"], "created": True}
        now = datetime.now(timezone.utc).isoformat()
        self._jobs[job_id] = {
            "job_id": job_id, "source_id": source_id, "revision_id": revision_id,
            "project": project, "file": file, "start_offset": start_offset,
            "end_offset": None, "payload_sha256": payload_sha256, "status": "running",
            "attempts": 1, "last_error": None, "result": None,
            "created_at": now, "updated_at": now, "completed_at": None,
        }
        return {"status": "running", "attempts": 1, "created": True}

    async def complete_job(self, job_id: str, end_offset: int, result: dict[str, Any]) -> dict[str, Any]:
        row = self._jobs.get(job_id)
        if row is None:
            raise ValueError("transcript job not found")
        now = datetime.now(timezone.utc).isoformat()
        row.update({
            "status": "completed", "end_offset": end_offset, "result": result,
            "last_error": None, "updated_at": now, "completed_at": now,
        })
        return dict(row)

    async def fail_job(self, job_id: str, error: str) -> None:
        row = self._jobs.get(job_id)
        if row is not None and row["status"] == "running":
            row.update({
                "status": "failed", "last_error": str(error)[:2000],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._jobs.get(job_id)
        return dict(row) if row else None

    async def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        return sorted(
            (dict(row) for row in self._jobs.values()),
            key=lambda row: row["updated_at"], reverse=True,
        )[:limit]

    async def recover_interrupted(self) -> int:
        recovered = 0
        for row in self._jobs.values():
            if row["status"] == "running":
                row.update({
                    "status": "interrupted",
                    "last_error": "Process restarted before the operation completed; resubmit the same slice to retry.",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })
                recovered += 1
        return recovered
