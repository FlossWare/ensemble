from __future__ import annotations

import argparse
import os

from .config import DEFAULT_HOST, DEFAULT_PORT, settings_from_env


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Ensemble REST server")
    parser.add_argument("--host", help=f"HTTP bind address (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, help=f"HTTP port (default: {DEFAULT_PORT})")
    parser.add_argument("--persistence-root", help="Root directory for Ensemble persistent data")
    parser.add_argument("--database-url", help="Optional SQLite URL override")
    args = parser.parse_args()

    if args.host is not None:
        os.environ["ENSEMBLE_HOST"] = args.host
    if args.port is not None:
        os.environ["ENSEMBLE_PORT"] = str(args.port)
    if args.persistence_root is not None:
        os.environ["ENSEMBLE_PERSISTENCE_ROOT"] = args.persistence_root
    if args.database_url is not None:
        os.environ["ENSEMBLE_DATABASE_URL"] = args.database_url

    settings = settings_from_env()
    import uvicorn
    from .api import create_app

    uvicorn.run(
        create_app(database_url=settings.database_url),
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
