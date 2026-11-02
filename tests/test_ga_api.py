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
