from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.store import InMemoryDocumentStore


def test_learning_outcome_is_idempotent_and_updates_memory_and_thompson_once():
    with TestClient(create_app(store=InMemoryDocumentStore())) as http:
        assert http.get("/api/v1/learning/health").json()["service"] == "learning"
        assert http.get("/api/v1/learning/ready").json()["ready"] is True
        assert http.get("/api/v1/learning/ping").json()["message"] == "pong"

        payload = {
            "task_id": "task-001", "task_type": "code-review", "model": "sonnet",
            "rating": 4, "tokens": 1200, "cost": 0.025,
        }
        first = http.post("/api/v1/learning/outcomes", json=payload)
        replay = http.post("/api/v1/learning/outcomes", json=payload)
        conflict = http.post(
            "/api/v1/learning/outcomes",
            json={**payload, "rating": 1},
        )
        assert first.status_code == 200
        assert first.json()["checkpoint_advanced"] is True
        assert replay.status_code == 200
        assert replay.json()["duplicate"] is True
        assert conflict.status_code == 409

        outcome = http.get("/api/v1/learning/outcomes/task-001").json()["outcome"]
        assert outcome["status"] == "completed"
        assert outcome["memory_recorded"] is True
        assert outcome["thompson_recorded"] is True
        memory_entries = http.get("/api/v1/memory/memories/learning-outcomes/entries").json()["entries"]
        assert len(memory_entries) == 1
        snapshots = http.get("/api/v1/memory/memories/thompson-state/entries").json()["entries"]
        assert len(snapshots) == 1
        thompson = http.get("/api/v1/thompson/state").json()["state"]["models"]["sonnet"]
        assert thompson["calls"] == 1
        assert thompson["successes"] == 1

        report = http.get("/api/v1/learning/report").json()
        assert report["total_outcomes"] == 1
        assert report["by_model"]["sonnet"]["avg_rating"] == 4.0


def test_learning_portable_artifacts_are_idempotent_and_conflicts_are_rejected():
    artifact = {
        "schema": "learning-artifact",
        "version": "0.1",
        "artifact_type": "ga-parameters",
        "source": "ensemble",
        "created_at": "2026-11-01T00:00:00+00:00",
        "provenance": {"run": "local"},
        "payload": {"run_id": "ga-run-1", "parameters": {"threshold": 0.4}},
    }
    with TestClient(create_app(store=InMemoryDocumentStore())) as http:
        first = http.post("/api/v1/learning/artifacts", json={"artifact": artifact})
        duplicate = http.post("/api/v1/learning/artifacts", json={"artifact": artifact})
        conflict = http.post(
            "/api/v1/learning/artifacts",
            json={"artifact": {**artifact, "payload": {"run_id": "ga-run-1", "parameters": {"threshold": 0.8}}}},
        )
        assert first.status_code == 200
        assert first.json()["artifact_status"] == "stored"
        assert duplicate.status_code == 200
        assert duplicate.json()["artifact_status"] == "duplicate"
        assert conflict.status_code == 409
        assert len(http.get("/api/v1/learning/artifacts").json()["artifacts"]) == 1
        assert len(http.get("/api/v1/memory/memories/learning-artifacts/entries").json()["entries"]) == 1


def test_learning_reset_requires_confirmation_and_preserves_thompson_state():
    with TestClient(create_app(store=InMemoryDocumentStore())) as http:
        http.post(
            "/api/v1/learning/outcomes",
            json={"task_id": "reset-1", "task_type": "test", "model": "haiku", "rating": 5, "tokens": 10, "cost": 0},
        )
        denied = http.post("/api/v1/learning/reset", json={"confirm": False})
        reset = http.post("/api/v1/learning/reset", json={"confirm": True})
        assert denied.status_code == 400
        assert reset.status_code == 200
        assert reset.json()["thompson_state_preserved"] is True
        assert http.get("/api/v1/learning/report").json()["total_outcomes"] == 0
        assert http.get("/api/v1/thompson/state").json()["state"]["models"]["haiku"]["calls"] == 1


def test_learning_poc_action_contract_is_retained():
    with TestClient(create_app(store=InMemoryDocumentStore())) as http:
        result = http.post(
            "/api/v1/learning",
            json={
                "op": "process_outcome", "task_id": "compat-1", "task_type": "testing",
                "model": "haiku", "rating": 3, "tokens": 50, "cost": 0.0,
            },
        )
        assert result.status_code == 200
        assert result.json()["checkpoint_advanced"] is True



def test_learning_outcome_does_not_advance_when_memory_returns_unacknowledged_status():
    from ensemble.memory_store import InMemoryMemoryStore

    class StatusFailureMemoryStore(InMemoryMemoryStore):
        def __init__(self):
            super().__init__()
            self.fail_snapshot = True

        async def append(self, name, payload, event_id=None):
            if name == "thompson-state" and self.fail_snapshot:
                return {"status": "failed"}
            return await super().append(name, payload, event_id)

    memory = StatusFailureMemoryStore()
    with TestClient(create_app(store=InMemoryDocumentStore(), memory_store=memory)) as http:
        payload = {
            "task_id": "snapshot-status-retry", "task_type": "testing",
            "model": "sonnet", "rating": 5, "tokens": 100, "cost": 0.01,
        }
        failed = http.post("/api/v1/learning/outcomes", json=payload)
        assert failed.status_code == 503
        pending = http.get("/api/v1/learning/outcomes/snapshot-status-retry").json()["outcome"]
        assert pending["status"] == "pending"
        assert pending["thompson_recorded"] is False
        assert http.get("/api/v1/thompson/state").json()["state"]["models"]["sonnet"]["calls"] == 1

        memory.fail_snapshot = False
        retried = http.post("/api/v1/learning/outcomes", json=payload)
        assert retried.status_code == 200
        assert retried.json()["checkpoint_advanced"] is True
        assert http.get("/api/v1/thompson/state").json()["state"]["models"]["sonnet"]["calls"] == 1
        snapshots = http.get("/api/v1/memory/memories/thompson-state/entries").json()["entries"]
        assert len(snapshots) == 1


def test_learning_artifact_does_not_acknowledge_unstored_memory_status():
    from ensemble.learning_store import InMemoryLearningStore
    from ensemble.memory_store import InMemoryMemoryStore

    class StatusFailureMemoryStore(InMemoryMemoryStore):
        async def append(self, name, payload, event_id=None):
            if name == "learning-artifacts":
                return {"status": "failed"}
            return await super().append(name, payload, event_id)

    artifact = {
        "schema": "learning-artifact", "version": "0.1",
        "artifact_type": "test.artifact", "source": "tests",
        "created_at": "2026-11-05T00:00:00+00:00", "provenance": {},
        "payload": {"run_id": "status-failure-1"},
    }
    with TestClient(create_app(
        store=InMemoryDocumentStore(), memory_store=StatusFailureMemoryStore(),
        learning_store=InMemoryLearningStore(),
    )) as http:
        failed = http.post("/api/v1/learning/artifacts", json={"artifact": artifact})
        assert failed.status_code == 503
        assert len(http.get("/api/v1/learning/artifacts").json()["artifacts"]) == 1



def test_in_memory_learning_artifacts_are_newest_first_like_sqlite():
    import asyncio
    from ensemble.learning_store import InMemoryLearningStore

    async def exercise():
        store = InMemoryLearningStore()
        base = {
            "schema": "learning-artifact", "version": "0.1",
            "artifact_type": "ga-parameters", "source": "tests",
            "created_at": "2026-11-05T00:00:00+00:00", "provenance": {},
            "payload": {"run_id": "run-1"},
        }
        await store.record_artifact(base)
        await store.record_artifact({**base, "payload": {"run_id": "run-2"}})
        artifacts = await store.list_artifacts(2)
        assert [item["artifact_key"] for item in artifacts] == [
            "ga-parameters:run-2", "ga-parameters:run-1",
        ]

    asyncio.run(exercise())
