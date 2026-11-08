from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol
from uuid import UUID

from .task_store import TaskRecord, TaskRequest, TaskSubmission


TaskHandler = Callable[[TaskRecord], Awaitable[dict[str, Any]]]


class TaskStore(Protocol):
    """Persistence contract used by the orchestrator, independent of transport."""

    def accept(
        self, request: TaskRequest, local_instance_id: str,
    ) -> tuple[FederatedTaskRecord, bool]: ...
    def claim_next(
        self, worker_id: str, task_types: list[str],
    ) -> FederatedTaskRecord | None: ...
    def complete(self, task_id: UUID, worker_id: str, result: dict[str, Any]) -> bool: ...
    def fail(self, task_id: UUID, worker_id: str, error: str) -> bool: ...
    def recover_interrupted(self) -> int: ...
    def get(self, task_id: UUID) -> FederatedTaskRecord | None: ...
    def list(self, limit: int = 100) -> list[FederatedTaskRecord]: ...


class Orchestrator:
    """Per-instance task lifecycle and execution authority.

    Network adapters submit work to this service. They never claim tasks or
    execute handlers themselves. The store is persistence only; scheduling,
    dispatch and handler execution are owned here.
    """

    def __init__(
        self,
        store: TaskStore,
        instance_id: str,
        *,
        poll_interval: float = 0.25,
        worker_count: int = 1,
    ) -> None:
        if not instance_id:
            raise ValueError("instance_id is required")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if isinstance(worker_count, bool) or not isinstance(worker_count, int) or not 1 <= worker_count <= 32:
            raise ValueError("worker_count must be between 1 and 32")
        self.store = store
        self.instance_id = instance_id
        self.poll_interval = poll_interval
        self.worker_count = worker_count
        self._handlers: dict[str, TaskHandler] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._started = False

    def register(self, task_type: str, handler: TaskHandler) -> None:
        if self._started:
            raise RuntimeError("handlers must be registered before the orchestrator starts")
        if not task_type or len(task_type) > 128:
            raise ValueError("task_type must be a non-empty string of at most 128 characters")
        if task_type in self._handlers:
            raise ValueError(f"handler already registered for {task_type}")
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._handlers[task_type] = handler

    def accepts(self, task_type: str) -> bool:
        return task_type in self._handlers

    async def accept_remote(
        self, request: FederatedTaskRequest,
    ) -> tuple[FederatedTaskRecord, bool]:
        if not self._started:
            raise RuntimeError("orchestrator is not running")
        if not self.accepts(request.task_type):
            raise ValueError(f"no execution handler registered for {request.task_type}")
        return await asyncio.to_thread(self.store.accept, request, self.instance_id)

    async def submit_local(
        self, submission: TaskSubmission,
    ) -> tuple[FederatedTaskRecord, bool]:
        if not self._started:
            raise RuntimeError("orchestrator is not running")
        if not self.accepts(submission.task_type):
            raise ValueError(f"no execution handler registered for {submission.task_type}")
        request = FederatedTaskRequest(
            **submission.model_dump(mode="python"),
            origin_instance_id=self.instance_id,
        )
        return await asyncio.to_thread(
            self.store.accept, request, self.instance_id, allow_local_origin=True,
        )

    async def get(self, task_id: UUID) -> FederatedTaskRecord | None:
        return await asyncio.to_thread(self.store.get, task_id)

    async def list(self, limit: int = 100) -> list[FederatedTaskRecord]:
        return await asyncio.to_thread(self.store.list, limit)

    async def start(self) -> int:
        if self._started:
            return 0
        recovered = await asyncio.to_thread(self.store.recover_interrupted)
        self._started = True
        self._workers = [
            asyncio.create_task(self._worker(index), name=f"ensemble-orchestrator-{index}")
            for index in range(self.worker_count)
        ]
        return recovered

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        workers, self._workers = self._workers, []
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    async def _worker(self, index: int) -> None:
        worker_id = f"{self.instance_id}:worker-{index}"
        task_types = sorted(self._handlers)
        while self._started:
            try:
                task = await asyncio.to_thread(self.store.claim_next, worker_id, task_types)
                if task is None:
                    await asyncio.sleep(self.poll_interval)
                    continue
                try:
                    if task.deadline is not None and task.deadline <= datetime.now(timezone.utc):
                        raise TimeoutError("task deadline expired before execution")
                    result = await self._handlers[task.task_type](task)
                    if not isinstance(result, dict):
                        raise TypeError("task handler must return an object")
                    await asyncio.to_thread(
                        self.store.complete, task.task_id, worker_id, result,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await asyncio.to_thread(
                        self.store.fail, task.task_id, worker_id,
                        f"{type(exc).__name__}: task execution failed",
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(max(self.poll_interval, 0.5))
