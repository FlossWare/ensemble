import asyncio
import time
from pathlib import Path
from uuid import uuid4

import pytest

from ensemble.federated_tasks import FederatedTaskRequest, FederatedTaskStore
from ensemble.orchestration import Orchestrator


def test_orchestrator_dispatches_registered_handler_and_persists_result(tmp_path: Path):
    async def scenario():
        store = FederatedTaskStore(tmp_path)
        store.initialize()
        orchestrator = Orchestrator(store, "local-instance", poll_interval=0.01)
        invoked = []

        async def handler(task):
            invoked.append(task.task_id)
            return {"answer": task.payload["value"] * 2}

        orchestrator.register("test.double", handler)
        await orchestrator.start()
        try:
            request = FederatedTaskRequest(
                task_id=uuid4(),
                idempotency_key="double-1",
                origin_instance_id="remote-instance",
                target_capability="compute",
                task_type="test.double",
                payload={"value": 21},
            )
            accepted, created = await orchestrator.accept_remote(request)
            assert created is True
            assert accepted.state == "accepted"

            deadline = time.monotonic() + 2
            result = None
            while time.monotonic() < deadline:
                result = await orchestrator.get(request.task_id)
                if result and result.state in {"succeeded", "failed"}:
                    break
                await asyncio.sleep(0.01)

            assert result is not None
            assert result.state == "succeeded"
            assert result.result == {"answer": 42}
            assert invoked == [request.task_id]
        finally:
            await orchestrator.stop()

    asyncio.run(scenario())


def test_orchestrator_requires_registered_handler_and_single_registration(tmp_path: Path):
    async def handler(task):
        return {"ok": True}

    store = FederatedTaskStore(tmp_path)
    orchestrator = Orchestrator(store, "local-instance")
    orchestrator.register("test.operation", handler)
    with pytest.raises(ValueError, match="already registered"):
        orchestrator.register("test.operation", handler)
    assert orchestrator.accepts("test.operation")
    assert not orchestrator.accepts("test.missing")


def test_orchestrator_rejects_invalid_worker_configuration(tmp_path: Path):
    with pytest.raises(ValueError, match="worker_count"):
        Orchestrator(FederatedTaskStore(tmp_path), "local-instance", worker_count=0)
