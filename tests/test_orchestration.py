import asyncio
import time
from pathlib import Path
from uuid import uuid4

import pytest

from ensemble.task_store import SqliteTaskStore, TaskRequest
from ensemble.orchestration import Orchestrator


def test_orchestrator_dispatches_registered_handler_and_persists_result(tmp_path: Path):
    async def scenario():
        store = SqliteTaskStore(tmp_path)
        store.initialize()
        orchestrator = Orchestrator(store, "local-instance", poll_interval=0.01)
        invoked = []

        async def handler(task):
            invoked.append(task.task_id)
            return {"answer": task.payload["value"] * 2}

        orchestrator.register("test.double", handler)
        await orchestrator.start()
        try:
            parent_task_id = uuid4()
            request = TaskRequest(
                task_id=uuid4(),
                parent_task_id=parent_task_id,
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
            assert result.parent_task_id == parent_task_id
            assert result.contract_version == 1
            assert invoked == [request.task_id]
        finally:
            await orchestrator.stop()

    asyncio.run(scenario())


def test_orchestrator_requires_registered_handler_and_single_registration(tmp_path: Path):
    async def handler(task):
        return {"ok": True}

    store = SqliteTaskStore(tmp_path)
    orchestrator = Orchestrator(store, "local-instance")
    orchestrator.register("test.operation", handler)
    with pytest.raises(ValueError, match="already registered"):
        orchestrator.register("test.operation", handler)
    assert orchestrator.accepts("test.operation")
    assert not orchestrator.accepts("test.missing")


def test_orchestrator_rejects_invalid_worker_configuration(tmp_path: Path):
    with pytest.raises(ValueError, match="worker_count"):
        Orchestrator(SqliteTaskStore(tmp_path), "local-instance", worker_count=0)


def test_task_store_migrates_legacy_federation_database_without_losing_tasks(tmp_path: Path):
    import hashlib
    import json
    import sqlite3
    from datetime import datetime, timezone

    from ensemble.task_store import SqliteTaskStore, TaskRecord, TaskRequest

    legacy_path = tmp_path / "federated-tasks.sqlite3"
    request = TaskRequest(
        idempotency_key="legacy-task-1",
        origin_instance_id="remote-instance",
        target_capability="search",
        task_type="search.query",
        payload={"query": "legacy task"},
    )
    now = datetime.now(timezone.utc)
    body = request.model_dump(mode="json")
    record = TaskRecord(
        **body, state="accepted", accepted_at=now, updated_at=now,
    )
    with sqlite3.connect(legacy_path) as db:
        db.execute("""
            CREATE TABLE federated_tasks (
                task_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_sha256 TEXT NOT NULL,
                record_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        db.execute(
            "INSERT INTO federated_tasks VALUES(?,?,?,?,?,?)",
            (
                str(request.task_id), request.idempotency_key,
                hashlib.sha256(b"legacy").hexdigest(), record.model_dump_json(),
                now.isoformat(), now.isoformat(),
            ),
        )

    store = SqliteTaskStore(tmp_path)
    store.initialize()

    migrated = store.get(request.task_id)
    assert migrated is not None
    assert migrated.task_id == request.task_id
    assert migrated.payload == {"query": "legacy task"}
    assert (tmp_path / "tasks.sqlite3").exists()
    assert not legacy_path.exists()



def test_task_contract_rejects_unsupported_version():
    from pydantic import ValidationError
    from ensemble.task_store import TaskRequest

    with pytest.raises(ValidationError):
        TaskRequest(
            contract_version=2,
            idempotency_key="unsupported-version",
            origin_instance_id="remote-instance",
            target_capability="search",
            task_type="search.query",
        )
