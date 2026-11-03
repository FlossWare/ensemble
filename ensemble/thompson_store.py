from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import Column, DateTime, Float, Integer, MetaData, String, Table, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.engine import make_url

from .partitioning import PartitionedAsyncEngine


thompson_metadata = MetaData()
thompson_models = Table(
    "ensemble_thompson_models", thompson_metadata,
    Column("model_name", String(256), primary_key=True),
    Column("successes", Integer, nullable=False),
    Column("failures", Integer, nullable=False),
    Column("total_cost", Float, nullable=False),
    Column("total_tokens", Integer, nullable=False),
    Column("calls", Integer, nullable=False),
    Column("last_updated", DateTime(timezone=True), nullable=False),
)
thompson_events = Table(
    "ensemble_thompson_events", thompson_metadata,
    Column("event_id", String(256), primary_key=True),
    Column("model_name", String(256), nullable=False),
    Column("payload_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


class ThompsonStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def get_state(self) -> dict[str, Any]: ...
    async def record_outcome(self, model: str, success: bool, cost: float, tokens: int) -> bool: ...
    async def record_outcome_once(self, event_id: str, model: str, success: bool, cost: float, tokens: int) -> bool: ...
    async def reset(self, model: str) -> bool: ...


class SqliteThompsonStore:
    def __init__(self, database_url: str | None = None):
        url_text = database_url or os.getenv("ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db")
        if make_url(url_text).get_backend_name() != "sqlite":
            raise ValueError("Ensemble Thompson persistence supports SQLite only")
        self.engine = PartitionedAsyncEngine(url_text, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            from sqlalchemy import inspect
            names = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        if not {"ensemble_thompson_models", "ensemble_thompson_events"}.issubset(names):
            raise RuntimeError("Thompson schema is not initialized; run 'alembic upgrade head'")

    async def close(self) -> None:
        await self.engine.dispose()

    async def get_state(self) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                select(thompson_models).order_by(thompson_models.c.model_name)
            )).mappings().all()
        models = {}
        for row in rows:
            item = dict(row)
            item["last_updated"] = item["last_updated"].isoformat()
            models[item["model_name"]] = item
        last_updated = max((item["last_updated"] for item in models.values()), default=datetime.now(timezone.utc).isoformat())
        return {"last_updated": last_updated, "models": models}

    async def record_outcome(self, model: str, success: bool, cost: float, tokens: int) -> bool:
        _validate_outcome(model, success, cost, tokens)
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            statement = sqlite_insert(thompson_models).values(
                model_name=model, successes=int(success), failures=int(not success),
                total_cost=float(cost), total_tokens=tokens, calls=1, last_updated=now,
            )
            await connection.execute(statement.on_conflict_do_update(
                index_elements=[thompson_models.c.model_name],
                set_={
                    "successes": thompson_models.c.successes + int(success),
                    "failures": thompson_models.c.failures + int(not success),
                    "total_cost": thompson_models.c.total_cost + float(cost),
                    "total_tokens": thompson_models.c.total_tokens + tokens,
                    "calls": thompson_models.c.calls + 1,
                    "last_updated": now,
                },
            ))
        return True

    async def record_outcome_once(self, event_id: str, model: str, success: bool, cost: float, tokens: int) -> bool:
        if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 256:
            raise ValueError("event_id must be a non-empty string of at most 256 characters")
        _validate_outcome(model, success, cost, tokens)
        canonical = json.dumps(
            {"model": model, "success": success, "cost": float(cost), "tokens": tokens},
            sort_keys=True, separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            result = await connection.execute(
                sqlite_insert(thompson_events).values(
                    event_id=event_id, model_name=model, payload_sha256=digest, created_at=now
                ).on_conflict_do_nothing(index_elements=[thompson_events.c.event_id])
            )
            if result.rowcount == 0:
                existing = await connection.scalar(
                    select(thompson_events.c.payload_sha256).where(thompson_events.c.event_id == event_id)
                )
                if existing != digest:
                    raise ValueError("Thompson event_id already exists with different outcome")
                return False
            statement = sqlite_insert(thompson_models).values(
                model_name=model, successes=int(success), failures=int(not success),
                total_cost=float(cost), total_tokens=tokens, calls=1, last_updated=now,
            )
            await connection.execute(statement.on_conflict_do_update(
                index_elements=[thompson_models.c.model_name],
                set_={
                    "successes": thompson_models.c.successes + int(success),
                    "failures": thompson_models.c.failures + int(not success),
                    "total_cost": thompson_models.c.total_cost + float(cost),
                    "total_tokens": thompson_models.c.total_tokens + tokens,
                    "calls": thompson_models.c.calls + 1,
                    "last_updated": now,
                },
            ))
        return True

    async def reset(self, model: str) -> bool:
        if not isinstance(model, str) or not model.strip() or len(model) > 256:
            raise ValueError("model must be a non-empty string of at most 256 characters")
        async with self.engine.begin() as connection:
            result = await connection.execute(
                update(thompson_models)
                .where(thompson_models.c.model_name == model)
                .values(successes=0, failures=0, total_cost=0.0, total_tokens=0,
                        calls=0, last_updated=datetime.now(timezone.utc))
            )
        return result.rowcount > 0


def _validate_outcome(model: Any, success: Any, cost: Any, tokens: Any) -> None:
    if not isinstance(model, str) or not model.strip() or len(model) > 256:
        raise ValueError("model must be a non-empty string of at most 256 characters")
    if not isinstance(success, bool):
        raise ValueError("success must be a boolean")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(float(cost)) or cost < 0:
        raise ValueError("cost must be a finite non-negative number")
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise ValueError("tokens must be a non-negative integer")


class InMemoryThompsonStore:
    def __init__(self):
        self.models: dict[str, dict[str, Any]] = {}
        self.events: dict[str, str] = {}

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def get_state(self) -> dict[str, Any]:
        models = {}
        for name, stats in sorted(self.models.items()):
            item = dict(stats)
            models[name] = item
        return {"last_updated": datetime.now(timezone.utc).isoformat(), "models": models}

    async def record_outcome(self, model: str, success: bool, cost: float, tokens: int) -> bool:
        _validate_outcome(model, success, cost, tokens)
        stats = self.models.setdefault(model, {
            "model_name": model, "successes": 0, "failures": 0,
            "total_cost": 0.0, "total_tokens": 0, "calls": 0,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })
        stats["successes"] += int(success)
        stats["failures"] += int(not success)
        stats["total_cost"] += float(cost)
        stats["total_tokens"] += tokens
        stats["calls"] += 1
        stats["last_updated"] = datetime.now(timezone.utc).isoformat()
        return True

    async def record_outcome_once(self, event_id: str, model: str, success: bool, cost: float, tokens: int) -> bool:
        if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 256:
            raise ValueError("event_id must be a non-empty string of at most 256 characters")
        _validate_outcome(model, success, cost, tokens)
        canonical = json.dumps(
            {"model": model, "success": success, "cost": float(cost), "tokens": tokens},
            sort_keys=True, separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        existing = self.events.get(event_id)
        if existing is not None:
            if existing != digest:
                raise ValueError("Thompson event_id already exists with different outcome")
            return False
        self.events[event_id] = digest
        await self.record_outcome(model, success, cost, tokens)
        return True

    async def reset(self, model: str) -> bool:
        if not isinstance(model, str) or not model.strip() or len(model) > 256:
            raise ValueError("model must be a non-empty string of at most 256 characters")
        if model not in self.models:
            return False
        self.models[model] = {
            "model_name": model, "successes": 0, "failures": 0,
            "total_cost": 0.0, "total_tokens": 0, "calls": 0,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        return True
