from __future__ import annotations

import asyncio
import contextvars
import hashlib
import ipaddress
import logging
import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


_PARTITION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MIGRATION_LOCKS: dict[str, threading.Lock] = {}
_MIGRATION_LOCKS_GUARD = threading.Lock()
_MIGRATED_DATABASES: set[str] = set()
_LOG = logging.getLogger("ensemble.partitioning")


@dataclass(frozen=True)
class RequestScope:
    principal_id: str = "local"
    partition_id: str = "default"
    authorization: str | None = None
    correlation_id: str | None = None


_request_scope: contextvars.ContextVar[RequestScope] = contextvars.ContextVar(
    "ensemble_request_scope", default=RequestScope()
)


def current_request_scope() -> RequestScope:
    return _request_scope.get()


def current_partition_id() -> str:
    return current_request_scope().partition_id


def set_request_scope(scope: RequestScope):
    return _request_scope.set(scope)


def reset_request_scope(token: contextvars.Token) -> None:
    _request_scope.reset(token)


def _partition_token_map() -> dict[str, dict[str, Any]]:
    raw = os.getenv("ENSEMBLE_PARTITION_TOKENS", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ENSEMBLE_PARTITION_TOKENS must be valid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("ENSEMBLE_PARTITION_TOKENS must be a JSON object")
    result: dict[str, dict[str, Any]] = {}
    for digest, principal in value.items():
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError("partition token keys must be lowercase SHA-256 hex digests")
        if not isinstance(principal, dict):
            raise RuntimeError("each partition token entry must be an object")
        subject = principal.get("subject")
        partitions = principal.get("partitions")
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 256:
            raise RuntimeError("each partition token entry requires a non-empty subject")
        if not isinstance(partitions, list) or not partitions:
            raise RuntimeError("each partition token entry requires a non-empty partitions list")
        normalized = []
        for partition_id in partitions:
            if not isinstance(partition_id, str) or not _PARTITION_PATTERN.fullmatch(partition_id):
                raise RuntimeError("partition IDs must be 1-128 safe characters")
            if partition_id not in normalized:
                normalized.append(partition_id)
        result[digest] = {"subject": subject, "partitions": normalized}
    return result


def _problem(status: int, title: str, detail: str, code: str) -> JSONResponse:
    _LOG.warning("partition_request_rejected status=%s code=%s", status, code)
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={"type": "about:blank", "title": title, "status": status,
                 "detail": detail, "code": code},
    )


def _is_public_path(path: str) -> bool:
    return path in {"/health", "/ready", "/openapi.json", "/docs", "/redoc", "/control", "/control.js"}


def _is_loopback_client(request: Request) -> bool:
    client = request.client
    if client is None:
        return False
    host = client.host.casefold()
    if host in {"testclient", "localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def partition_scope_middleware(request: Request, call_next):
    supplied_correlation_id = request.headers.get("x-correlation-id", "")
    correlation_id = supplied_correlation_id if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", supplied_correlation_id) else uuid4().hex
    scope = RequestScope(correlation_id=correlation_id)
    if request.url.path.startswith("/api/v1/") and not _is_public_path(request.url.path):
        try:
            token_map = _partition_token_map()
        except RuntimeError as exc:
            return _problem(503, "Partition authentication misconfigured", str(exc), "partition_auth_misconfigured")
        authorization = request.headers.get("authorization")
        if token_map:
            scheme, _, credential = (authorization or "").partition(" ")
            if scheme.casefold() != "bearer" or not credential.strip():
                return _problem(401, "Authentication required", "A valid Bearer token is required.", "authentication_required")
            digest = hashlib.sha256(credential.strip().encode("utf-8")).hexdigest()
            principal = token_map.get(digest)
            if principal is None:
                return _problem(401, "Invalid credentials", "The supplied credentials are not valid.", "invalid_credentials")
            allowed = principal["partitions"]
            requested = request.headers.get("x-ensemble-partition")
            if requested is None:
                if len(allowed) != 1:
                    return _problem(400, "Partition required", "Select one authorized partition with X-Ensemble-Partition.", "partition_required")
                requested = allowed[0]
            if requested not in allowed:
                return _problem(403, "Partition access denied", "The caller is not authorized for the requested partition.", "partition_forbidden")
            scope = RequestScope(
                principal_id=principal["subject"], partition_id=requested,
                authorization=f"Bearer {credential.strip()}",
                correlation_id=request.headers.get("x-correlation-id"),
            )
        else:
            if os.getenv("ENSEMBLE_AUTH_REQUIRED", "0").strip() == "1":
                return _problem(503, "Authentication unavailable", "Authentication is required but no partition token map is configured.", "partition_auth_unconfigured")
            if not _is_loopback_client(request):
                return _problem(503, "Authentication required", "Remote API access requires configured partition credentials.", "remote_auth_required")
            requested = request.headers.get("x-ensemble-partition")
            if requested not in (None, "default"):
                return _problem(403, "Partition access denied", "Unauthenticated local mode can access only the default partition.", "partition_forbidden")
            if authorization:
                return _problem(401, "Authentication unavailable", "Bearer authentication is not configured.", "authentication_unconfigured")
    request.state.principal_id = scope.principal_id
    request.state.partition_id = scope.partition_id
    request.state.correlation_id = scope.correlation_id
    token = set_request_scope(scope)
    try:
        response = await call_next(request)
        response.headers.setdefault("X-Correlation-ID", scope.correlation_id or "")
        _LOG.info(
            "partition_request principal=%s partition=%s method=%s status=%s correlation_id=%s",
            scope.principal_id, scope.partition_id, request.method,
            response.status_code, scope.correlation_id,
        )
        return response
    except Exception:
        _LOG.exception(
            "partition_request_failed principal=%s partition=%s method=%s correlation_id=%s",
            scope.principal_id, scope.partition_id, request.method, scope.correlation_id,
        )
        raise
    finally:
        reset_request_scope(token)


def partition_database_url(database_url: str, partition_id: str) -> str:
    if not isinstance(partition_id, str) or not _PARTITION_PATTERN.fullmatch(partition_id):
        raise ValueError("partition_id must contain 1-128 safe characters")
    base = make_url(database_url)
    if base.get_backend_name() != "sqlite":
        raise ValueError("Ensemble partition databases support SQLite only")
    if partition_id == "default":
        return database_url
    if base.database in (None, "", ":memory:"):
        raise ValueError("named partitions require a durable SQLite database path")
    root = Path(base.database).expanduser().resolve().parent
    digest = hashlib.sha256(partition_id.encode("utf-8")).hexdigest()
    target = root / "partitions" / f"{digest}.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    return base.set(database=str(target)).render_as_string(hide_password=False)


def _ensure_partition_schema(database_url: str, partition_id: str) -> None:
    parsed = make_url(database_url)
    database = Path(parsed.database or "").expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    key = str(database)
    with _MIGRATION_LOCKS_GUARD:
        lock = _MIGRATION_LOCKS.setdefault(key, threading.Lock())
    with lock:
        if key not in _MIGRATED_DATABASES:
            ini = Path(__file__).resolve().parents[1] / "alembic.ini"
            config = Config(str(ini))
            config.attributes["database_url"] = database_url
            command.upgrade(config, "head")
        connection = sqlite3.connect(key)
        try:
            row = connection.execute(
                "SELECT partition_id FROM ensemble_partition_metadata LIMIT 1"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO ensemble_partition_metadata(partition_id) VALUES (?)",
                    (partition_id,),
                )
                connection.commit()
            elif row[0] == "default" and partition_id != "default":
                connection.execute(
                    "UPDATE ensemble_partition_metadata SET partition_id = ?",
                    (partition_id,),
                )
                connection.commit()
            elif row[0] != partition_id:
                raise RuntimeError("partition database identity does not match its requested scope")
        finally:
            connection.close()
        _MIGRATED_DATABASES.add(key)


class _PartitionedEngineContext:
    def __init__(self, owner: "PartitionedAsyncEngine", method: str):
        self.owner = owner
        self.method = method
        self.context = None

    async def __aenter__(self):
        engine = await self.owner._engine_for_current_scope()
        self.context = getattr(engine, self.method)()
        return await self.context.__aenter__()

    async def __aexit__(self, exc_type, exc, tb):
        return await self.context.__aexit__(exc_type, exc, tb)


class PartitionedAsyncEngine:
    """Resolve each request scope to its own durable SQLite database file."""

    def __init__(self, database_url: str, *, pool_pre_ping: bool = True):
        self.url = make_url(database_url)
        if self.url.get_backend_name() != "sqlite":
            raise ValueError("Ensemble partition databases support SQLite only")
        self.database_url = database_url
        self.pool_pre_ping = pool_pre_ping
        self._engines: dict[str, AsyncEngine] = {}
        self._lock = asyncio.Lock()

    async def _engine_for_current_scope(self) -> AsyncEngine:
        partition_id = current_partition_id()
        url_text = partition_database_url(self.database_url, partition_id)
        async with self._lock:
            engine = self._engines.get(partition_id)
            if engine is not None:
                return engine
            if partition_id != "default":
                await asyncio.to_thread(_ensure_partition_schema, url_text, partition_id)
            engine = create_async_engine(url_text, pool_pre_ping=self.pool_pre_ping)
            self._engines[partition_id] = engine
            return engine

    def connect(self):
        return _PartitionedEngineContext(self, "connect")

    def begin(self):
        return _PartitionedEngineContext(self, "begin")

    async def dispose(self) -> None:
        async with self._lock:
            engines = list(self._engines.values())
            self._engines.clear()
        for engine in engines:
            await engine.dispose()
