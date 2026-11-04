from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, JSON, MetaData, String, Table, Text, delete, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.engine import make_url

from .partitioning import PartitionedAsyncEngine


learning_metadata = MetaData()
learning_outcomes = Table(
    "ensemble_learning_outcomes", learning_metadata,
    Column("task_id", String(256), primary_key=True),
    Column("task_type", String(128), nullable=False),
    Column("model", String(256), nullable=False),
    Column("rating", Integer, nullable=False),
    Column("tokens", Integer, nullable=False),
    Column("cost", Float, nullable=False),
    Column("payload_sha256", String(64), nullable=False),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("status", String(32), nullable=False),
    Column("memory_recorded", Boolean, nullable=False),
    Column("thompson_recorded", Boolean, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
learning_artifacts = Table(
    "ensemble_learning_artifacts", learning_metadata,
    Column("artifact_key", String(512), primary_key=True),
    Column("artifact_type", String(128), nullable=False),
    Column("source", String(256), nullable=False),
    Column("schema", String(64), nullable=False),
    Column("version", String(32), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("provenance", JSON, nullable=False),
    Column("created_at", String(128), nullable=False),
    Column("payload_sha256", String(64), nullable=False),
    Column("persisted_at", DateTime(timezone=True), nullable=False),
)


def _validate_outcome(task_id: Any, task_type: Any, model: Any, rating: Any, tokens: Any, cost: Any) -> dict[str, Any]:
    for value, label, maximum in (
        (task_id, "task_id", 256), (task_type, "task_type", 128), (model, "model", 256)
    ):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{label} must be a non-empty string of at most {maximum} characters")
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        raise ValueError("rating must be an integer between 1 and 5")
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise ValueError("tokens must be a non-negative integer")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(float(cost)) or cost < 0:
        raise ValueError("cost must be a finite non-negative number")
    return {"task_id": task_id, "task_type": task_type, "model": model,
            "rating": rating, "tokens": tokens, "cost": float(cost)}


def _validate_artifact(value: Any) -> tuple[dict[str, Any], str, str]:
    if not isinstance(value, dict):
        raise ValueError("artifact must be an object")
    if value.get("schema") != "learning-artifact" or value.get("version") != "0.1":
        raise ValueError("unsupported learning artifact schema or version")
    artifact_type, source = value.get("artifact_type"), value.get("source")
    payload, provenance = value.get("payload"), value.get("provenance", {})
    if not isinstance(artifact_type, str) or not artifact_type.strip() or len(artifact_type) > 128:
        raise ValueError("artifact_type must be a non-empty string")
    if not isinstance(source, str) or not source.strip() or len(source) > 256:
        raise ValueError("source must be a non-empty string")
    if not isinstance(payload, dict) or not isinstance(provenance, dict):
        raise ValueError("artifact payload and provenance must be objects")
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 256:
        raise ValueError("artifact.payload.run_id is required")
    created_at = value.get("created_at")
    if not isinstance(created_at, str) or not created_at.strip() or len(created_at) > 128:
        raise ValueError("artifact.created_at is required")
    try:
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("artifact must contain finite JSON-compatible values") from exc
    return value, f"{artifact_type}:{run_id}", hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LearningStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def prepare_outcome(self, **values: Any) -> dict[str, Any]: ...
    async def complete_outcome(self, task_id: str, memory_recorded: bool, thompson_recorded: bool) -> None: ...
    async def get_outcome(self, task_id: str) -> dict[str, Any] | None: ...
    async def recent_outcomes(self, days: int = 7) -> list[dict[str, Any]]: ...
    async def report(self) -> dict[str, Any]: ...
    async def reset(self) -> bool: ...
    async def record_artifact(self, artifact: dict[str, Any]) -> str: ...
    async def list_artifacts(self, limit: int = 50) -> list[dict[str, Any]]: ...


class SqliteLearningStore:
    def __init__(self, database_url: str | None = None):
        url_text = database_url or os.getenv("ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db")
        if make_url(url_text).get_backend_name() != "sqlite":
            raise ValueError("Ensemble Learning persistence supports SQLite only")
        self.engine = PartitionedAsyncEngine(url_text, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            from sqlalchemy import inspect
            names = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        if not {"ensemble_learning_outcomes", "ensemble_learning_artifacts"}.issubset(names):
            raise RuntimeError("Learning schema is not initialized; run 'alembic upgrade head'")

    async def close(self) -> None:
        await self.engine.dispose()

    async def prepare_outcome(self, **values: Any) -> dict[str, Any]:
        payload = _validate_outcome(
            values.get("task_id"), values.get("task_type"), values.get("model"),
            values.get("rating"), values.get("tokens"), values.get("cost"),
        )
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            result = await connection.execute(
                sqlite_insert(learning_outcomes).values(
                    **payload, payload_sha256=digest, timestamp=now, status="pending",
                    memory_recorded=False, thompson_recorded=False, updated_at=now,
                ).on_conflict_do_nothing(index_elements=[learning_outcomes.c.task_id])
            )
            row = (await connection.execute(
                select(learning_outcomes).where(learning_outcomes.c.task_id == payload["task_id"])
            )).mappings().one()
        record = _public_outcome(dict(row))
        if record["payload_sha256"] != digest:
            raise ValueError("task_id already exists with a different outcome payload")
        return {"status": "created" if result.rowcount == 1 else ("duplicate" if record["status"] == "completed" else "pending"), "outcome": record}

    async def complete_outcome(self, task_id: str, memory_recorded: bool, thompson_recorded: bool) -> None:
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            await connection.execute(
                update(learning_outcomes).where(learning_outcomes.c.task_id == task_id)
                .values(status="completed", memory_recorded=memory_recorded,
                        thompson_recorded=thompson_recorded, updated_at=now)
            )

    async def get_outcome(self, task_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(
                select(learning_outcomes).where(learning_outcomes.c.task_id == task_id)
            )).mappings().first()
        return _public_outcome(dict(row)) if row else None

    async def recent_outcomes(self, days: int = 7) -> list[dict[str, Any]]:
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650:
            raise ValueError("days must be an integer between 1 and 3650")
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                select(learning_outcomes).where(learning_outcomes.c.timestamp >= cutoff)
                .order_by(learning_outcomes.c.timestamp.desc())
            )).mappings().all()
        return [_public_outcome(dict(row)) for row in rows]

    async def report(self) -> dict[str, Any]:
        recent = await self.recent_outcomes(30)
        if not recent:
            return {"ok": True, "total_outcomes": 0, "report": "No outcomes recorded yet"}
        by_model: dict[str, dict[str, float]] = {}
        by_task: dict[str, dict[str, float]] = {}
        for item in recent:
            model, task = item["model"], item["task_type"]
            by_model.setdefault(model, {"count": 0, "rating": 0, "cost": 0.0})
            by_task.setdefault(task, {"count": 0, "rating": 0})
            by_model[model]["count"] += 1
            by_model[model]["rating"] += item["rating"]
            by_model[model]["cost"] += item["cost"]
            by_task[task]["count"] += 1
            by_task[task]["rating"] += item["rating"]
        return {
            "ok": True, "total_outcomes": len(recent), "date_range": "30 days",
            "by_model": {
                name: {"count": int(stats["count"]), "avg_rating": round(stats["rating"] / stats["count"], 2),
                       "total_cost": round(stats["cost"], 4)}
                for name, stats in by_model.items()
            },
            "by_task_type": {
                name: {"count": int(stats["count"]), "avg_rating": round(stats["rating"] / stats["count"], 2)}
                for name, stats in by_task.items()
            },
        }

    async def reset(self) -> bool:
        async with self.engine.begin() as connection:
            await connection.execute(delete(learning_outcomes))
        return True

    async def record_artifact(self, artifact: dict[str, Any]) -> str:
        value, key, digest = _validate_artifact(artifact)
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            result = await connection.execute(
                sqlite_insert(learning_artifacts).values(
                    artifact_key=key, artifact_type=value["artifact_type"], source=value["source"],
                    schema=value["schema"], version=value["version"], payload=value["payload"],
                    provenance=value.get("provenance", {}), created_at=value["created_at"],
                    payload_sha256=digest, persisted_at=now,
                ).on_conflict_do_nothing(index_elements=[learning_artifacts.c.artifact_key])
            )
            if result.rowcount == 1:
                return "stored"
            existing = await connection.scalar(
                select(learning_artifacts.c.payload_sha256).where(learning_artifacts.c.artifact_key == key)
            )
            if existing != digest:
                raise ValueError("artifact idempotency key already exists with different content")
            return "duplicate"

    async def list_artifacts(self, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                select(learning_artifacts).order_by(learning_artifacts.c.persisted_at.desc()).limit(limit)
            )).mappings().all()
        return [{
            "artifact_key": row["artifact_key"], "artifact_type": row["artifact_type"],
            "source": row["source"], "schema": row["schema"], "version": row["version"],
            "payload": row["payload"], "provenance": row["provenance"],
            "created_at": row["created_at"], "payload_sha256": row["payload_sha256"],
            "persisted_at": row["persisted_at"].isoformat(),
        } for row in rows]


def _public_outcome(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for field in ("timestamp", "updated_at"):
        if result.get(field) is not None:
            result[field] = result[field].isoformat()
    result["memory_recorded"] = bool(result["memory_recorded"])
    result["thompson_recorded"] = bool(result["thompson_recorded"])
    return result


class InMemoryLearningStore:
    def __init__(self):
        self.outcomes: dict[str, dict[str, Any]] = {}
        self.artifacts: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def prepare_outcome(self, **values: Any) -> dict[str, Any]:
        payload = _validate_outcome(
            values.get("task_id"), values.get("task_type"), values.get("model"),
            values.get("rating"), values.get("tokens"), values.get("cost"),
        )
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        existing = self.outcomes.get(payload["task_id"])
        if existing:
            if existing["payload_sha256"] != digest:
                raise ValueError("task_id already exists with a different outcome payload")
            return {"status": "duplicate" if existing["status"] == "completed" else "pending", "outcome": dict(existing)}
        row = {**payload, "payload_sha256": digest, "timestamp": now, "status": "pending",
               "memory_recorded": False, "thompson_recorded": False, "updated_at": now}
        self.outcomes[payload["task_id"]] = row
        return {"status": "created", "outcome": dict(row)}

    async def complete_outcome(self, task_id: str, memory_recorded: bool, thompson_recorded: bool) -> None:
        row = self.outcomes[task_id]
        row.update(status="completed", memory_recorded=memory_recorded, thompson_recorded=thompson_recorded,
                   updated_at=datetime.now(timezone.utc).isoformat())

    async def get_outcome(self, task_id: str) -> dict[str, Any] | None:
        return dict(self.outcomes[task_id]) if task_id in self.outcomes else None

    async def recent_outcomes(self, days: int = 7) -> list[dict[str, Any]]:
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650:
            raise ValueError("days must be an integer between 1 and 3650")
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = []
        for item in self.outcomes.values():
            timestamp = datetime.fromisoformat(item["timestamp"])
            if timestamp >= cutoff:
                result.append(dict(item))
        return sorted(result, key=lambda item: item["timestamp"], reverse=True)

    async def report(self) -> dict[str, Any]:
        recent = await self.recent_outcomes(30)
        if not recent:
            return {"ok": True, "total_outcomes": 0, "report": "No outcomes recorded yet"}
        by_model: dict[str, dict[str, float]] = {}
        by_task: dict[str, dict[str, float]] = {}
        for item in recent:
            model, task = item["model"], item["task_type"]
            by_model.setdefault(model, {"count": 0, "rating": 0, "cost": 0.0})
            by_task.setdefault(task, {"count": 0, "rating": 0})
            by_model[model]["count"] += 1
            by_model[model]["rating"] += item["rating"]
            by_model[model]["cost"] += item["cost"]
            by_task[task]["count"] += 1
            by_task[task]["rating"] += item["rating"]
        return {
            "ok": True, "total_outcomes": len(recent), "date_range": "30 days",
            "by_model": {name: {"count": int(stats["count"]), "avg_rating": round(stats["rating"] / stats["count"], 2), "total_cost": round(stats["cost"], 4)} for name, stats in by_model.items()},
            "by_task_type": {name: {"count": int(stats["count"]), "avg_rating": round(stats["rating"] / stats["count"], 2)} for name, stats in by_task.items()},
        }

    async def reset(self) -> bool:
        self.outcomes.clear()
        return True

    async def record_artifact(self, artifact: dict[str, Any]) -> str:
        value, key, digest = _validate_artifact(artifact)
        existing = self.artifacts.get(key)
        if existing:
            if existing["payload_sha256"] != digest:
                raise ValueError("artifact idempotency key already exists with different content")
            return "duplicate"
        self.artifacts[key] = {"artifact_key": key, **value, "payload_sha256": digest,
                               "persisted_at": datetime.now(timezone.utc).isoformat()}
        return "stored"

    async def list_artifacts(self, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return list(self.artifacts.values())[-limit:]
