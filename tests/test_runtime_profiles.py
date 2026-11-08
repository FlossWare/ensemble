from __future__ import annotations

import sys

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
