from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import make_url


DEFAULT_PORT = 8090
DEFAULT_HOST = "127.0.0.1"


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    persistence_root: Path
    database_url: str


def persistence_root_from_env() -> Path:
    configured = os.getenv("ENSEMBLE_PERSISTENCE_ROOT", ".ensemble")
    if not configured.strip():
        raise ValueError("ENSEMBLE_PERSISTENCE_ROOT must not be empty")
    root = Path(configured).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_database_url(database_url: str | None = None) -> str:
    root = persistence_root_from_env()
    configured = database_url or os.getenv("ENSEMBLE_DATABASE_URL")
    if configured:
        url_text = configured
    else:
        url_text = f"sqlite+aiosqlite:///{(root / 'ensemble.db').as_posix()}"
    url = make_url(url_text)
    if url.get_backend_name() != "sqlite":
        raise ValueError("Ensemble supports SQLite persistence only")
    if url.database not in (None, "", ":memory:"):
        database_path = Path(url.database).expanduser().resolve()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        url = url.set(database=str(database_path))
    return url.render_as_string(hide_password=False)


def settings_from_env(database_url: str | None = None) -> Settings:
    host = os.getenv("ENSEMBLE_HOST", DEFAULT_HOST).strip()
    if not host:
        raise ValueError("ENSEMBLE_HOST must not be empty")
    if host.casefold() not in {"127.0.0.1", "::1", "localhost"} and not os.getenv("ENSEMBLE_PARTITION_TOKENS", "").strip():
        raise ValueError("non-loopback listeners require ENSEMBLE_PARTITION_TOKENS authentication")
    raw_port = os.getenv("ENSEMBLE_PORT", str(DEFAULT_PORT))
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("ENSEMBLE_PORT must be an integer between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise ValueError("ENSEMBLE_PORT must be an integer between 1 and 65535")
    root = persistence_root_from_env()
    return Settings(
        host=host,
        port=port,
        persistence_root=root,
        database_url=resolve_database_url(database_url),
    )
