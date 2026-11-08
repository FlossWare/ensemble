from __future__ import annotations

import asyncio
import inspect
import os
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field


WorkerHandler = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]


class WorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)
    capability: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    deadline_unix_ms: int | None = Field(default=None, ge=1)


class WorkerResponse(BaseModel):
    request_id: str
    worker_id: str
    capability: str
    operation: str
    result: dict[str, Any]
    elapsed_ms: float


@dataclass(frozen=True)
class WorkerOperation:
    capability: str
    operation: str
    description: str
    handler: WorkerHandler
    contract_version: int = 1


class WorkerRuntime:
    """Small, stateless execution runtime; it does not initialize Ensemble stores."""

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        name: str | None = None,
        max_concurrency: int | None = None,
        labels: dict[str, str] | None = None,
    ) -> None:
        self.worker_id = worker_id or os.getenv("ENSEMBLE_WORKER_ID") or str(uuid4())
        self.name = name or os.getenv("ENSEMBLE_WORKER_NAME") or socket.gethostname()
        configured = max_concurrency if max_concurrency is not None else int(
            os.getenv("ENSEMBLE_WORKER_MAX_CONCURRENCY", "2")
        )
        if configured < 1 or configured > 256:
            raise ValueError("max_concurrency must be between 1 and 256")
        self.max_concurrency = configured
        self.labels = dict(labels or {})
        self._operations: dict[tuple[str, str], WorkerOperation] = {}
        self._semaphore = asyncio.Semaphore(configured)
        self._active = 0
        self._completed = 0
        self._failed = 0

    def register(
        self,
        capability: str,
        operation: str,
        handler: WorkerHandler,
        *,
        description: str = "",
        contract_version: int = 1,
    ) -> None:
        key = (capability, operation)
        if not capability or not operation:
            raise ValueError("capability and operation are required")
        if key in self._operations:
            raise ValueError(f"operation already registered: {capability}.{operation}")
        if contract_version < 1:
            raise ValueError("contract_version must be positive")
        self._operations[key] = WorkerOperation(
            capability=capability,
            operation=operation,
            description=description,
            handler=handler,
            contract_version=contract_version,
        )

    def catalogue(self) -> list[dict[str, Any]]:
        return [
            {
                "capability": item.capability,
                "operation": item.operation,
                "description": item.description,
                "contract_version": item.contract_version,
            }
            for _, item in sorted(self._operations.items())
        ]

    def status(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "name": self.name,
            "status": "ready" if self._operations else "idle",
            "max_concurrency": self.max_concurrency,
            "active": self._active,
            "completed": self._completed,
            "failed": self._failed,
            "capabilities": self.catalogue(),
            "labels": self.labels,
            "resources": {
                "cpu_count": os.cpu_count() or 1,
                "memory_limit_bytes": _memory_limit_bytes(),
            },
        }

    async def invoke(self, request: WorkerRequest) -> WorkerResponse:
        operation = self._operations.get((request.capability, request.operation))
        if operation is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "worker_operation_not_found",
                    "capability": request.capability,
                    "operation": request.operation,
                },
            )
        if request.deadline_unix_ms is not None and int(time.time() * 1000) >= request.deadline_unix_ms:
            raise HTTPException(status_code=408, detail={"code": "worker_deadline_expired"})

        async with self._semaphore:
            if request.deadline_unix_ms is not None and int(time.time() * 1000) >= request.deadline_unix_ms:
                raise HTTPException(status_code=408, detail={"code": "worker_deadline_expired"})
            started = time.perf_counter()
            self._active += 1
            try:
                if inspect.iscoroutinefunction(operation.handler):
                    pending = operation.handler(request.payload)
                else:
                    pending = asyncio.to_thread(operation.handler, request.payload)
                if request.deadline_unix_ms is None:
                    result = await pending
                else:
                    remaining = max(0.0, (request.deadline_unix_ms / 1000) - time.time())
                    result = await asyncio.wait_for(pending, timeout=remaining)
                if inspect.isawaitable(result):
                    result = await result
                if not isinstance(result, dict):
                    raise TypeError("worker handlers must return a JSON object")
                self._completed += 1
                return WorkerResponse(
                    request_id=request.request_id,
                    worker_id=self.worker_id,
                    capability=operation.capability,
                    operation=operation.operation,
                    result=result,
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                )
            except asyncio.TimeoutError as exc:
                self._failed += 1
                raise HTTPException(status_code=408, detail={"code": "worker_deadline_expired"}) from exc
            except HTTPException:
                self._failed += 1
                raise
            except Exception as exc:
                self._failed += 1
                raise HTTPException(
                    status_code=500,
                    detail={"code": "worker_execution_failed", "error": str(exc)[:512]},
                ) from exc
            finally:
                self._active -= 1


def create_worker_app(runtime: WorkerRuntime | None = None) -> FastAPI:
    worker = runtime or WorkerRuntime()
    app = FastAPI(title="Ensemble Worker Runtime", version="0.1")
    app.state.worker_runtime = worker

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "role": "worker"}

    @app.get("/ready")
    async def ready() -> dict[str, Any]:
        state = worker.status()
        if not state["capabilities"]:
            raise HTTPException(status_code=503, detail="worker has no registered operations")
        return {"status": "ready", "worker_id": worker.worker_id}

    @app.get("/worker/status")
    async def status() -> dict[str, Any]:
        return worker.status()

    @app.get("/worker/capabilities")
    async def capabilities() -> dict[str, Any]:
        return {
            "worker_id": worker.worker_id,
            "contract_version": 1,
            "operations": worker.catalogue(),
        }

    @app.post("/worker/invoke", response_model=WorkerResponse)
    async def invoke(request: WorkerRequest) -> WorkerResponse:
        return await worker.invoke(request)

    return app


def _memory_limit_bytes() -> int | None:
    """Return a Linux cgroup memory limit when present, otherwise None."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path, encoding="ascii") as source:
                value = source.read().strip()
        except OSError:
            continue
        if value == "max":
            return None
        try:
            limit = int(value)
        except ValueError:
            continue
        if 0 < limit < (1 << 60):
            return limit
    return None


app = create_worker_app()


def main() -> None:
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the lightweight Ensemble Worker Runtime")
    parser.add_argument("--host", default=os.getenv("ENSEMBLE_WORKER_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ENSEMBLE_WORKER_PORT", "8091")))
    args = parser.parse_args()
    uvicorn.run("ensemble.worker_runtime:app", host=args.host, port=args.port)
