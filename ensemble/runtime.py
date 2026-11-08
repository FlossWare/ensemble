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

    Standard and full currently share the existing full application composition.
    Their subsystem-level composition is intentionally not faked: splitting those
    dependencies is a follow-on refactor. Worker remains independently importable.
    """
    selected = RuntimeProfile(profile or os.getenv("ENSEMBLE_PROFILE", "full"))
    if selected is RuntimeProfile.WORKER:
        from .worker_runtime import configured_worker_runtime, create_worker_app

        return create_worker_app(configured_worker_runtime())

    # Importing api is deliberately deferred so worker deployments do not import
    # SQLAlchemy, Chroma, graph, memory, GA, Thompson, or learning subsystems.
    from .api import create_app

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
