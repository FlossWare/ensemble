import time

from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.store import InMemoryDocumentStore


def _wait_for_run(http, run_id: str):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = http.get(f"/api/v1/ga/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()["run"]
        if run["status"] in {"completed", "failed"}:
            return run
        time.sleep(0.02)
    raise AssertionError(f"GA run {run_id} did not reach a terminal state")


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

        run = _wait_for_run(http, run_id)
        assert run["status"] == "completed"
        assert run["result"]["total_evaluations"] == 24
        assert run["result"]["evaluation_scope"]["externally_validated"] is False
        assert set(run["result"]["best_by_system"]) == {
            "compression", "thompson", "caching", "matrix", "dashboard"
        }
        task = http.get(f"/api/v1/tasks/{run_id}")
        assert task.status_code == 200
        assert task.json()["task_type"] == "ga.run"
        assert task.json()["state"] == "succeeded"


def test_ga_promotion_requires_explicit_approval_and_never_applies_live_settings():
    with TestClient(create_app(store=InMemoryDocumentStore())) as http:
        started = http.post(
            "/api/v1/ga/runs",
            json={"population_size": 15, "generations": 1, "seed": 4321},
        )
        run_id = started.json()["run"]["id"]
        _wait_for_run(http, run_id)

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
