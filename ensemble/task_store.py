from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


TaskState = Literal["accepted", "running", "succeeded", "failed", "cancelled"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("task payload must be JSON serializable") from exc
    if len(encoded.encode("utf-8")) > 1_048_576:
        raise ValueError("task payload exceeds 1 MiB")
    return encoded


class TaskRequest(BaseModel):
    contract_version: Literal[1] = 1
    task_id: UUID = Field(default_factory=uuid4)
    parent_task_id: UUID | None = None
    idempotency_key: str = Field(min_length=1, max_length=256)
    origin_instance_id: str = Field(min_length=1, max_length=128)
    target_capability: str = Field(min_length=1, max_length=128)
    task_type: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deadline: datetime | None = None
    correlation_id: str | None = Field(default=None, max_length=256)
    causation_id: str | None = Field(default=None, max_length=256)

    @field_validator("created_at", "deadline")
    @classmethod
    def require_timezone(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must include a timezone")
        return value

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, value):
        _canonical(value)
        return value


class TaskRecord(BaseModel):
    contract_version: Literal[1] = 1
    task_id: UUID
    parent_task_id: UUID | None = None
    idempotency_key: str
    origin_instance_id: str
    target_capability: str
    task_type: str
    payload: dict[str, Any]
    state: TaskState
    created_at: datetime
    accepted_at: datetime
    updated_at: datetime
    deadline: datetime | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    progress: dict[str, Any] = Field(default_factory=dict)
    worker_id: str | None = None
    attempt: int = 0


class DelegatedTaskReference(BaseModel):
    contract_version: Literal[1] = 1
    parent_task_id: UUID
    peer_instance_id: str = Field(min_length=1, max_length=128)
    task_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=256)
    target_capability: str = Field(min_length=1, max_length=128)
    task_type: str = Field(min_length=1, max_length=128)
    state: Literal["submitted", "accepted", "rejected"] = "submitted"
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class SqliteTaskStore:
    """Durable per-instance task persistence and single-owner state transitions."""

    def __init__(self, persistence_root: Path) -> None:
        self.path = Path(persistence_root) / "tasks.sqlite3"

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path = self.path.parent / "federated-tasks.sqlite3"
        if legacy_path.exists() and not self.path.exists():
            with sqlite3.connect(legacy_path, timeout=10) as legacy:
                legacy.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            os.replace(legacy_path, self.path)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("PRAGMA journal_mode=WAL")
            existing = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ensemble_tasks'"
            ).fetchone()
            legacy_table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='federated_tasks'"
            ).fetchone()
            if existing is None and legacy_table is not None:
                db.execute("ALTER TABLE federated_tasks RENAME TO ensemble_tasks")
            db.execute("""
                CREATE TABLE IF NOT EXISTS ensemble_tasks (
                    task_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS ix_ensemble_tasks_created ON ensemble_tasks(created_at)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS ensemble_delegated_tasks (
                    peer_instance_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    parent_task_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(peer_instance_id, task_id),
                    UNIQUE(parent_task_id, peer_instance_id, idempotency_key)
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS ix_delegated_tasks_parent ON ensemble_delegated_tasks(parent_task_id, created_at)")

    def accept(
        self, request: TaskRequest, local_instance_id: str,
        *, allow_local_origin: bool = False,
    ) -> tuple[TaskRecord, bool]:
        if request.origin_instance_id == local_instance_id and not allow_local_origin:
            raise ValueError("origin_instance_id must identify a remote instance")
        body = request.model_dump(mode="json")
        fingerprint = {key: value for key, value in body.items() if key != "created_at"}
        digest = hashlib.sha256(_canonical(fingerprint).encode("utf-8")).hexdigest()
        now = _now()
        record = TaskRecord(
            **body, state="accepted",
            accepted_at=now, updated_at=now,
        )
        encoded = record.model_dump_json()
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT request_sha256, record_json FROM ensemble_tasks WHERE idempotency_key=? OR task_id=?",
                (request.idempotency_key, str(request.task_id)),
            ).fetchone()
            if existing:
                if existing[0] != digest:
                    raise ValueError("task identity or idempotency key was reused with a different request")
                return TaskRecord.model_validate_json(existing[1]), False
            db.execute(
                "INSERT INTO ensemble_tasks(task_id,idempotency_key,request_sha256,record_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (str(request.task_id), request.idempotency_key, digest, encoded, now, now),
            )
        return record, True

    def claim_next(self, worker_id: str, task_types: list[str]) -> TaskRecord | None:
        if not task_types:
            return None
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT task_id, record_json FROM ensemble_tasks ORDER BY created_at, task_id"
            ).fetchall()
            for task_id, encoded in rows:
                record = TaskRecord.model_validate_json(encoded)
                if record.state != "accepted" or record.task_type not in task_types:
                    continue
                record.state = "running"
                record.worker_id = worker_id
                record.attempt += 1
                record.updated_at = datetime.now(timezone.utc)
                db.execute(
                    "UPDATE ensemble_tasks SET record_json=?, updated_at=? WHERE task_id=?",
                    (record.model_dump_json(), _now(), task_id),
                )
                return record
        return None

    def complete(self, task_id: UUID, worker_id: str, result: dict[str, Any]) -> bool:
        _canonical(result)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT record_json FROM ensemble_tasks WHERE task_id=?", (str(task_id),)
            ).fetchone()
            if row is None:
                return False
            record = TaskRecord.model_validate_json(row[0])
            if record.state != "running" or record.worker_id != worker_id:
                return False
            record.state = "succeeded"
            record.result = result
            record.error = None
            record.worker_id = None
            record.updated_at = datetime.now(timezone.utc)
            db.execute(
                "UPDATE ensemble_tasks SET record_json=?, updated_at=? WHERE task_id=?",
                (record.model_dump_json(), _now(), str(task_id)),
            )
            return True

    def fail(self, task_id: UUID, worker_id: str, error: str) -> bool:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT record_json FROM ensemble_tasks WHERE task_id=?", (str(task_id),)
            ).fetchone()
            if row is None:
                return False
            record = TaskRecord.model_validate_json(row[0])
            if record.state != "running" or record.worker_id != worker_id:
                return False
            record.state = "failed"
            record.error = str(error)[:1024]
            record.worker_id = None
            record.updated_at = datetime.now(timezone.utc)
            db.execute(
                "UPDATE ensemble_tasks SET record_json=?, updated_at=? WHERE task_id=?",
                (record.model_dump_json(), _now(), str(task_id)),
            )
            return True

    def recover_interrupted(self) -> int:
        recovered = 0
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT task_id, record_json FROM ensemble_tasks"
            ).fetchall()
            for task_id, encoded in rows:
                record = TaskRecord.model_validate_json(encoded)
                if record.state != "running":
                    continue
                record.state = "accepted"
                record.worker_id = None
                record.updated_at = datetime.now(timezone.utc)
                db.execute(
                    "UPDATE ensemble_tasks SET record_json=?, updated_at=? WHERE task_id=?",
                    (record.model_dump_json(), _now(), task_id),
                )
                recovered += 1
        return recovered

    def record_delegated_child(
        self,
        parent_task_id: UUID,
        peer_instance_id: str,
        submission: TaskSubmission,
        local_instance_id: str,
    ) -> DelegatedTaskReference:
        now = datetime.now(timezone.utc)
        reference = DelegatedTaskReference(
            parent_task_id=parent_task_id,
            peer_instance_id=peer_instance_id,
            task_id=submission.task_id,
            idempotency_key=submission.idempotency_key,
            target_capability=submission.target_capability,
            task_type=submission.task_type,
            state="submitted",
            created_at=now,
            updated_at=now,
        )
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            parent_row = db.execute(
                "SELECT record_json FROM ensemble_tasks WHERE task_id=?",
                (str(parent_task_id),),
            ).fetchone()
            if parent_row is None:
                raise ValueError("parent task does not exist on this instance")
            parent = TaskRecord.model_validate_json(parent_row[0])
            if parent.origin_instance_id != local_instance_id:
                raise ValueError("parent task is not owned by this instance")
            existing = db.execute(
                "SELECT record_json FROM ensemble_delegated_tasks WHERE peer_instance_id=? AND task_id=?",
                (peer_instance_id, str(submission.task_id)),
            ).fetchone()
            if existing:
                current = DelegatedTaskReference.model_validate_json(existing[0])
                if (
                    current.parent_task_id != parent_task_id
                    or current.idempotency_key != submission.idempotency_key
                    or current.target_capability != submission.target_capability
                    or current.task_type != submission.task_type
                ):
                    raise ValueError("delegated task identity was reused with different parent or request metadata")
                return current
            encoded = reference.model_dump_json()
            db.execute(
                "INSERT INTO ensemble_delegated_tasks(peer_instance_id,task_id,parent_task_id,idempotency_key,record_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (
                    peer_instance_id, str(submission.task_id), str(parent_task_id),
                    submission.idempotency_key, encoded, now.isoformat(), now.isoformat(),
                ),
            )
        return reference

    def update_delegated_child(
        self,
        peer_instance_id: str,
        task_id: UUID,
        *,
        state: Literal["accepted", "rejected"],
        error: str | None = None,
    ) -> DelegatedTaskReference | None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT record_json FROM ensemble_delegated_tasks WHERE peer_instance_id=? AND task_id=?",
                (peer_instance_id, str(task_id)),
            ).fetchone()
            if row is None:
                return None
            reference = DelegatedTaskReference.model_validate_json(row[0])
            reference.state = state
            reference.error = error[:1024] if error else None
            reference.updated_at = datetime.now(timezone.utc)
            encoded = reference.model_dump_json()
            db.execute(
                "UPDATE ensemble_delegated_tasks SET record_json=?, updated_at=? WHERE peer_instance_id=? AND task_id=?",
                (encoded, reference.updated_at.isoformat(), peer_instance_id, str(task_id)),
            )
            return reference

    def list_delegated_children(self, parent_task_id: UUID) -> list[DelegatedTaskReference]:
        with sqlite3.connect(self.path, timeout=10) as db:
            rows = db.execute(
                "SELECT record_json FROM ensemble_delegated_tasks WHERE parent_task_id=? ORDER BY created_at, peer_instance_id, task_id",
                (str(parent_task_id),),
            ).fetchall()
        return [DelegatedTaskReference.model_validate_json(row[0]) for row in rows]

    def get(self, task_id: UUID) -> TaskRecord | None:
        with sqlite3.connect(self.path, timeout=10) as db:
            row = db.execute("SELECT record_json FROM ensemble_tasks WHERE task_id=?", (str(task_id),)).fetchone()
        return TaskRecord.model_validate_json(row[0]) if row else None

    def list(self, limit: int = 100) -> list[TaskRecord]:
        bounded = max(1, min(int(limit), 500))
        with sqlite3.connect(self.path, timeout=10) as db:
            rows = db.execute(
                "SELECT record_json FROM ensemble_tasks ORDER BY created_at DESC, task_id LIMIT ?",
                (bounded,),
            ).fetchall()
        return [TaskRecord.model_validate_json(row[0]) for row in rows]


class TaskSubmission(BaseModel):
    contract_version: Literal[1] = 1
    task_id: UUID = Field(default_factory=uuid4)
    parent_task_id: UUID | None = None
    idempotency_key: str = Field(min_length=1, max_length=256)
    target_capability: str = Field(min_length=1, max_length=128)
    task_type: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deadline: datetime | None = None
    correlation_id: str | None = Field(default=None, max_length=256)
    causation_id: str | None = Field(default=None, max_length=256)

    @field_validator("created_at", "deadline")
    @classmethod
    def require_timezone(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must include a timezone")
        return value

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, value):
        _canonical(value)
        return value
