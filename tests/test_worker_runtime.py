from __future__ import annotations

import time

from fastapi.testclient import TestClient

from ensemble.worker_runtime import WorkerRuntime, create_worker_app


def test_worker_runtime_exposes_health_capabilities_and_status():
    runtime = WorkerRuntime(worker_id="worker-01", name="r9000", max_concurrency=3)
    runtime.register("provider", "models.complete", lambda payload: {"text": payload["prompt"]})

    with TestClient(create_worker_app(runtime)) as client:
        assert client.get("/health").json() == {"status": "ok", "role": "worker"}
        assert client.get("/ready").status_code == 200
        catalogue = client.get("/worker/capabilities").json()
        assert catalogue["worker_id"] == "worker-01"
        assert catalogue["operations"] == [{
            "capability": "provider",
            "operation": "models.complete",
            "description": "",
            "contract_version": 1,
        }]
        status = client.get("/worker/status").json()
        assert status["max_concurrency"] == 3
        assert status["resources"]["cpu_count"] >= 1


def test_worker_runtime_invokes_sync_and_async_handlers():
    runtime = WorkerRuntime(worker_id="worker-02")
    runtime.register("provider", "sync", lambda payload: {"value": payload["value"]})
    async def async_handler(payload):
        return {"value": payload["value"] * 2}
    runtime.register("provider", "async", async_handler)

    with TestClient(create_worker_app(runtime)) as client:
        sync_result = client.post("/worker/invoke", json={
            "request_id": "sync-1", "capability": "provider",
            "operation": "sync", "payload": {"value": 7},
        })
        async_result = client.post("/worker/invoke", json={
            "request_id": "async-1", "capability": "provider",
            "operation": "async", "payload": {"value": 7},
        })
        assert sync_result.status_code == 200
        assert sync_result.json()["result"] == {"value": 7}
        assert sync_result.json()["worker_id"] == "worker-02"
        assert async_result.status_code == 200
        assert async_result.json()["result"] == {"value": 14}
        assert client.get("/worker/status").json()["completed"] == 2


def test_worker_runtime_rejects_unknown_operations_and_expired_deadlines():
    runtime = WorkerRuntime()
    runtime.register("provider", "models.complete", lambda payload: {"ok": True})

    with TestClient(create_worker_app(runtime)) as client:
        missing = client.post("/worker/invoke", json={
            "capability": "unknown", "operation": "run", "payload": {},
        })
        expired = client.post("/worker/invoke", json={
            "capability": "provider", "operation": "models.complete",
            "payload": {}, "deadline_unix_ms": int(time.time() * 1000) - 1,
        })
        assert missing.status_code == 404
        assert expired.status_code == 408


def test_worker_runtime_requires_registered_operation_for_readiness():
    with TestClient(create_worker_app(WorkerRuntime())) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503


def test_worker_runtime_rejects_duplicate_and_invalid_concurrency():
    runtime = WorkerRuntime(max_concurrency=1)
    runtime.register("provider", "models.complete", lambda payload: {})
    try:
        runtime.register("provider", "models.complete", lambda payload: {})
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("duplicate operation was accepted")

    try:
        WorkerRuntime(max_concurrency=0)
    except ValueError as exc:
        assert "between 1 and 256" in str(exc)
    else:
        raise AssertionError("invalid concurrency was accepted")
