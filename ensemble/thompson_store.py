from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import Column, DateTime, Float, Integer, MetaData, String, Table, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.engine import make_url


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


class ThompsonStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def get_state(self) -> dict[str, Any]: ...
    async def record_outcome(self, model: str, success: bool, cost: float, tokens: int) -> bool: ...
    async def reset(self, model: str) -> bool: ...


class SqliteThompsonStore:
    def __init__(self, database_url: str | None = None):
        url_text = database_url or os.getenv("ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db")
        if make_url(url_text).get_backend_name() != "sqlite":
            raise ValueError("Ensemble Thompson persistence supports SQLite only")
        self.engine: AsyncEngine = create_async_engine(url_text, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            from sqlalchemy import inspect
            names = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        if "ensemble_thompson_models" not in names:
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
            item["success_rate"] = item["successes"] / item["calls"] if item["calls"] else 0.5
            item["avg_cost"] = item["total_cost"] / item["calls"] if item["calls"] else 0.0
            models[item["model_name"]] = item
        return {"last_updated": datetime.now(timezone.utc).isoformat(), "models": models}

    async def record_outcome(self, model: str, success: bool, cost: float, tokens: int) -> bool:
        _validate_outcome(model, success, cost, tokens)
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as connection:
            existing = (await connection.execute(
                select(thompson_models).where(thompson_models.c.model_name == model)
            )).mappings().first()
            values = {
                "model_name": model,
                "successes": (existing["successes"] if existing else 0) + int(success),
                "failures": (existing["failures"] if existing else 0) + int(not success),
                "total_cost": (existing["total_cost"] if existing else 0.0) + float(cost),
                "total_tokens": (existing["total_tokens"] if existing else 0) + tokens,
                "calls": (existing["calls"] if existing else 0) + 1,
                "last_updated": now,
            }
            await connection.execute(
                sqlite_insert(thompson_models).values(**values)
                .on_conflict_do_update(
                    index_elements=[thompson_models.c.model_name],
                    set_={key: value for key, value in values.items() if key != "model_name"},
                )
            )
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

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def get_state(self) -> dict[str, Any]:
        models = {}
        for name, stats in sorted(self.models.items()):
            item = dict(stats)
            item["success_rate"] = item["successes"] / item["calls"] if item["calls"] else 0.5
            item["avg_cost"] = item["total_cost"] / item["calls"] if item["calls"] else 0.0
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
