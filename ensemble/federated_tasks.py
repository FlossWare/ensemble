from __future__ import annotations

import hashlib
import json
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


class FederatedTaskRequest(BaseModel):
    task_id: UUID = Field(default_factory=uuid4)
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


class FederatedTaskRecord(BaseModel):
    task_id: UUID
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


class FederatedTaskStore:
    """Durable per-instance task inbox and single-owner task state transitions."""

    def __init__(self, persistence_root: Path) -> None:
        self.path = Path(persistence_root) / "federated-tasks.sqlite3"

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS federated_tasks (
                    task_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS ix_federated_tasks_created ON federated_tasks(created_at)")

    def accept(self, request: FederatedTaskRequest, local_instance_id: str) -> tuple[FederatedTaskRecord, bool]:
        if request.origin_instance_id == local_instance_id:
            raise ValueError("origin_instance_id must identify a remote instance")
        body = request.model_dump(mode="json")
        fingerprint = {key: value for key, value in body.items() if key != "created_at"}
        digest = hashlib.sha256(_canonical(fingerprint).encode("utf-8")).hexdigest()
        now = _now()
        record = FederatedTaskRecord(
            **body, state="accepted",
            accepted_at=now, updated_at=now,
        )
        encoded = record.model_dump_json()
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT request_sha256, record_json FROM federated_tasks WHERE idempotency_key=? OR task_id=?",
                (request.idempotency_key, str(request.task_id)),
            ).fetchone()
            if existing:
                if existing[0] != digest:
                    raise ValueError("task identity or idempotency key was reused with a different request")
                return FederatedTaskRecord.model_validate_json(existing[1]), False
            db.execute(
                "INSERT INTO federated_tasks(task_id,idempotency_key,request_sha256,record_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (str(request.task_id), request.idempotency_key, digest, encoded, now, now),
            )
        return record, True

    def claim_next(self, worker_id: str, task_types: list[str]) -> FederatedTaskRecord | None:
        if not task_types:
            return None
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT task_id, record_json FROM federated_tasks ORDER BY created_at, task_id"
            ).fetchall()
            for task_id, encoded in rows:
                record = FederatedTaskRecord.model_validate_json(encoded)
                if record.state != "accepted" or record.task_type not in task_types:
                    continue
                record.state = "running"
                record.worker_id = worker_id
                record.attempt += 1
                record.updated_at = datetime.now(timezone.utc)
                db.execute(
                    "UPDATE federated_tasks SET record_json=?, updated_at=? WHERE task_id=?",
                    (record.model_dump_json(), _now(), task_id),
                )
                return record
        return None

    def complete(self, task_id: UUID, worker_id: str, result: dict[str, Any]) -> bool:
        _canonical(result)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT record_json FROM federated_tasks WHERE task_id=?", (str(task_id),)
            ).fetchone()
            if row is None:
                return False
            record = FederatedTaskRecord.model_validate_json(row[0])
            if record.state != "running" or record.worker_id != worker_id:
                return False
            record.state = "succeeded"
            record.result = result
            record.error = None
            record.worker_id = None
            record.updated_at = datetime.now(timezone.utc)
            db.execute(
                "UPDATE federated_tasks SET record_json=?, updated_at=? WHERE task_id=?",
                (record.model_dump_json(), _now(), str(task_id)),
            )
            return True

    def fail(self, task_id: UUID, worker_id: str, error: str) -> bool:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT record_json FROM federated_tasks WHERE task_id=?", (str(task_id),)
            ).fetchone()
            if row is None:
                return False
            record = FederatedTaskRecord.model_validate_json(row[0])
            if record.state != "running" or record.worker_id != worker_id:
                return False
            record.state = "failed"
            record.error = str(error)[:1024]
            record.worker_id = None
            record.updated_at = datetime.now(timezone.utc)
            db.execute(
                "UPDATE federated_tasks SET record_json=?, updated_at=? WHERE task_id=?",
                (record.model_dump_json(), _now(), str(task_id)),
            )
            return True

    def recover_interrupted(self) -> int:
        recovered = 0
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT task_id, record_json FROM federated_tasks"
            ).fetchall()
            for task_id, encoded in rows:
                record = FederatedTaskRecord.model_validate_json(encoded)
                if record.state != "running":
                    continue
                record.state = "accepted"
                record.worker_id = None
                record.updated_at = datetime.now(timezone.utc)
                db.execute(
                    "UPDATE federated_tasks SET record_json=?, updated_at=? WHERE task_id=?",
                    (record.model_dump_json(), _now(), task_id),
                )
                recovered += 1
        return recovered

    def get(self, task_id: UUID) -> FederatedTaskRecord | None:
        with sqlite3.connect(self.path, timeout=10) as db:
            row = db.execute("SELECT record_json FROM federated_tasks WHERE task_id=?", (str(task_id),)).fetchone()
        return FederatedTaskRecord.model_validate_json(row[0]) if row else None

    def list(self, limit: int = 100) -> list[FederatedTaskRecord]:
        bounded = max(1, min(int(limit), 500))
        with sqlite3.connect(self.path, timeout=10) as db:
            rows = db.execute(
                "SELECT record_json FROM federated_tasks ORDER BY created_at DESC, task_id LIMIT ?",
                (bounded,),
            ).fetchall()
        return [FederatedTaskRecord.model_validate_json(row[0]) for row in rows]
