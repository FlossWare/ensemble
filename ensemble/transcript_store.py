from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import Column, DateTime, Integer, JSON, MetaData, String, Table, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.engine import make_url

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


class TranscriptCheckpointStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def get(self, source_id: str, revision_id: str) -> dict[str, Any] | None: ...
    async def commit(
        self, source_id: str, revision_id: str, project: str, file: str,
        start_offset: int, end_offset: int, payload_sha256: str,
        result: dict[str, Any],
    ) -> dict[str, Any]: ...


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
        self.engine: AsyncEngine = create_async_engine(self.database_url, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            names = await connection.run_sync(
                lambda conn: __import__("sqlalchemy").inspect(conn).get_table_names()
            )
        if "ensemble_transcript_checkpoints" not in names:
            raise RuntimeError("transcript checkpoint schema is not initialized; run 'alembic upgrade head'")

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
                if start_offset != 0:
                    raise _checkpoint_mismatch(0)
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


class InMemoryTranscriptCheckpointStore:
    def __init__(self):
        self._items: dict[tuple[str, str], dict[str, Any]] = {}

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
        if row is None:
            if start_offset != 0:
                raise _checkpoint_mismatch(0)
        else:
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
