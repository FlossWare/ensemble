from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.store import InMemoryDocumentStore


def test_ga_run_is_persisted_and_exposes_five_evaluators():
    with TestClient(create_app(store=InMemoryDocumentStore())) as http:
        optimizers = http.get("/api/v1/ga/optimizers")
        assert optimizers.status_code == 200
        assert set(optimizers.json()["systems"]) == {
            "compression", "thompson", "caching", "matrix", "dashboard"
        }

        started = http.post(
            "/api/v1/ga/runs",
            json={"population_size": 15, "generations": 1, "seed": 1234},
        )
        assert started.status_code == 202
        run_id = started.json()["run"]["id"]

        fetched = http.get(f"/api/v1/ga/runs/{run_id}")
        assert fetched.status_code == 200
        run = fetched.json()["run"]
        assert run["status"] == "completed"
        assert run["result"]["total_evaluations"] == 24
        assert run["result"]["evaluation_scope"]["externally_validated"] is False
        assert set(run["result"]["best_by_system"]) == {
            "compression", "thompson", "caching", "matrix", "dashboard"
        }


def test_ga_promotion_requires_explicit_approval_and_never_applies_live_settings():
    with TestClient(create_app(store=InMemoryDocumentStore())) as http:
        started = http.post(
            "/api/v1/ga/runs",
            json={"population_size": 15, "generations": 1, "seed": 4321},
        )
        run_id = started.json()["run"]["id"]

        denied = http.post(
            f"/api/v1/ga/runs/{run_id}/promote",
            json={"system": "compression", "rank": 1, "reason": "reviewed", "approved_by": "operator"},
        )
        assert denied.status_code == 400

        approved = http.post(
            f"/api/v1/ga/runs/{run_id}/promote",
            json={
                "approved": True,
                "system": "compression",
                "rank": 1,
                "reason": "candidate reviewed for export",
                "approved_by": "local-operator",
            },
        )
        assert approved.status_code == 200
        assert approved.json()["applied"] is False
        assert approved.json()["promotion"]["status"] == "approved_for_export"


def test_ga_run_rejects_invalid_bounds_and_unknown_configuration():
    with TestClient(create_app(store=InMemoryDocumentStore())) as http:
        too_small = http.post(
            "/api/v1/ga/runs",
            json={"population_size": 10, "generations": 1},
        )
        unknown = http.post(
            "/api/v1/ga/runs",
            json={"population_size": 15, "generations": 1, "arbitrary": True},
        )
        assert too_small.status_code == 400
        assert unknown.status_code == 400


def test_ga_run_can_be_recorded_as_learning_artifact_with_retry_after_memory_failure():
    from ensemble.ga_store import InMemoryGAStore
    from ensemble.learning_store import InMemoryLearningStore
    from ensemble.memory_store import InMemoryMemoryStore

    class FailOnceMemoryStore(InMemoryMemoryStore):
        def __init__(self):
            super().__init__()
            self.fail = True

        async def append(self, name, payload, event_id=None):
            if name == "learning-artifacts" and self.fail:
                self.fail = False
                raise RuntimeError("simulated Memory acknowledgement failure")
            return await super().append(name, payload, event_id)

    memory = FailOnceMemoryStore()
    learning = InMemoryLearningStore()
    ga = InMemoryGAStore()
    with TestClient(create_app(
        store=InMemoryDocumentStore(), memory_store=memory,
        learning_store=learning, ga_store=ga,
    )) as http:
        started = http.post("/api/v1/ga/runs", json={
            "population_size": 15, "generations": 1, "seed": 1234,
        })
        assert started.status_code == 202
        run_id = started.json()["run"]["id"]
        assert http.get(f"/api/v1/ga/runs/{run_id}").json()["run"]["status"] == "completed"

        failed = http.post(f"/api/v1/ga/runs/{run_id}/artifact", json={})
        retried = http.post(f"/api/v1/ga/runs/{run_id}/artifact", json={})
        assert failed.status_code == 503
        assert retried.status_code == 200
        assert retried.json()["artifact_status"] == "duplicate"
        assert retried.json()["memory"] is True
        artifacts = http.get("/api/v1/learning/artifacts").json()["artifacts"]
        assert len(artifacts) == 1
        assert artifacts[0]["artifact_type"] == "ga.tuning.result"
        entries = http.get("/api/v1/memory/memories/learning-artifacts/entries").json()["entries"]
        assert len(entries) == 1
        assert entries[0]["event_type"] == "learning.artifact"

