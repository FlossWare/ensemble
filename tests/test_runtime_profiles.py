from __future__ import annotations

import sys

from fastapi.testclient import TestClient

from ensemble.store import InMemoryDocumentStore
from ensemble.runtime import RuntimeProfile, create_runtime


def test_worker_profile_composes_without_importing_full_api():
    # The worker composition must not import the full application module.
    sys.modules.pop("ensemble.api", None)
    app = create_runtime(RuntimeProfile.WORKER)
    assert app.title == "Ensemble Worker Runtime"
    assert "ensemble.api" not in sys.modules


def test_runtime_profile_names_are_stable():
    assert [profile.value for profile in RuntimeProfile] == ["worker", "standard", "full"]


def test_standard_profile_omits_optional_ga_and_learning_capabilities():
    from ensemble.api import create_app

    app = create_app(profile=RuntimeProfile.STANDARD, run_reclamation_worker=False)
    assert app.state.runtime_profile == "standard"
    assert app.state.ga_store is None
    assert app.state.learning_store is None
    assert "ga" not in app.state.enabled_capability_groups
    assert "learning" not in app.state.enabled_capability_groups
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert not any(path.startswith("/api/v1/ga/") for path in paths)
    assert not any(path.startswith("/api/v1/learning/") for path in paths)
    assert "/api/v1/runtime/capabilities" in paths
    assert "/api/v1/runtime/capabilities/health" in paths



def test_thompson_store_uses_shared_capability_lifecycle():
    from ensemble.api import create_app

    app = create_app(
        profile=RuntimeProfile.STANDARD,
        store=InMemoryDocumentStore(),
        run_reclamation_worker=False,
    )
    for name in ("memory", "thompson"):
        descriptor = app.state.capability_registry.get(name).descriptor
        assert descriptor.profiles == (RuntimeProfile.STANDARD, RuntimeProfile.FULL)

    with TestClient(app) as client:
        assert set(app.state.capability_lifecycle.started) == {"memory", "thompson"}
        response = client.get("/api/v1/runtime/capabilities/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["profile"] == "standard"
        for name in ("memory", "thompson"):
            status = next(item for item in payload["capabilities"] if item["capability"] == name)
            assert status["status"] == "ready"

    assert app.state.capability_lifecycle.started == ()
