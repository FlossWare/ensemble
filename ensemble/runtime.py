from __future__ import annotations

import argparse
import os
from fastapi import FastAPI

from .core import CapabilityRegistry, RuntimeProfile


def create_runtime(
    profile: RuntimeProfile | str | None = None,
    *,
    capability_registry: CapabilityRegistry | None = None,
) -> FastAPI:
    """Compose the selected runtime without importing full subsystems for workers.

    Standard excludes the optional GA and Learning stores and route groups; Full
    adds them. Other legacy subsystems remain shared pending further extraction.
    Worker remains independently importable and never imports the full API.
    """
    selected = RuntimeProfile(profile or os.getenv("ENSEMBLE_PROFILE", "full"))
    if selected is RuntimeProfile.WORKER:
        from .worker_runtime import configured_worker_runtime, create_worker_app

        return create_worker_app(configured_worker_runtime())

    # api.py retains its legacy module-level ASGI app. Set the selected profile
    # during first import so that compatibility app does not eagerly construct
    # optional stores for a Standard deployment.
    previous_profile = os.environ.get("ENSEMBLE_PROFILE")
    os.environ["ENSEMBLE_PROFILE"] = selected.value
    try:
        from .api import create_app
    finally:
        if previous_profile is None:
            os.environ.pop("ENSEMBLE_PROFILE", None)
        else:
            os.environ["ENSEMBLE_PROFILE"] = previous_profile

    app = create_app(profile=selected, capability_registry=capability_registry)
    app.state.runtime_profile = selected.value
    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Run an Ensemble deployment profile")
    parser.add_argument(
        "--profile",
        choices=[item.value for item in RuntimeProfile],
        default=os.getenv("ENSEMBLE_PROFILE", RuntimeProfile.FULL.value),
    )
    parser.add_argument("--host", default=os.getenv("ENSEMBLE_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ENSEMBLE_PORT", "8080")))
    args = parser.parse_args()
    uvicorn.run(create_runtime(args.profile), host=args.host, port=args.port)
