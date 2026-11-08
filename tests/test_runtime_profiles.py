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
