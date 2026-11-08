from pathlib import Path

import pytest

from ensemble.config import DEFAULT_HOST, DEFAULT_PORT, resolve_database_url, settings_from_env


def test_default_settings_use_configurable_persistence_root(tmp_path, monkeypatch):
    root = tmp_path / "ensemble-data"
    monkeypatch.setenv("ENSEMBLE_PERSISTENCE_ROOT", str(root))
    monkeypatch.delenv("ENSEMBLE_DATABASE_URL", raising=False)
    monkeypatch.delenv("ENSEMBLE_HOST", raising=False)
    monkeypatch.delenv("ENSEMBLE_PORT", raising=False)

    settings = settings_from_env()

    assert settings.host == DEFAULT_HOST
    assert settings.port == DEFAULT_PORT
    assert settings.persistence_root == root.resolve()
    assert Path(settings.persistence_root / "ensemble.db").parent.is_dir()
    assert settings.database_url.endswith("/ensemble-data/ensemble.db")


def test_host_and_port_are_configurable_and_validated(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_PERSISTENCE_ROOT", str(tmp_path))
    monkeypatch.setenv("ENSEMBLE_HOST", "0.0.0.0")
    monkeypatch.setenv("ENSEMBLE_PORT", "9123")
    monkeypatch.setenv("ENSEMBLE_PARTITION_TOKENS", '{"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa":{"subject":"test-client","partitions":["test"]}}')
    settings = settings_from_env()
    assert settings.host == "0.0.0.0"
    assert settings.port == 9123

    monkeypatch.setenv("ENSEMBLE_PORT", "70000")
    with pytest.raises(ValueError, match="ENSEMBLE_PORT"):
        settings_from_env()


def test_non_loopback_listener_can_run_without_partition_credentials_for_local_federation(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_PERSISTENCE_ROOT", str(tmp_path))
    monkeypatch.setenv("ENSEMBLE_HOST", "0.0.0.0")
    monkeypatch.delenv("ENSEMBLE_PARTITION_TOKENS", raising=False)
    settings = settings_from_env()
    assert settings.host == "0.0.0.0"


def test_database_url_override_must_remain_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_PERSISTENCE_ROOT", str(tmp_path))
    monkeypatch.delenv("ENSEMBLE_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="SQLite"):
        resolve_database_url("postgresql+asyncpg://localhost/ensemble")


def test_explicit_database_url_override_is_used(tmp_path, monkeypatch):
    root = tmp_path / "root"
    db = tmp_path / "custom" / "ensemble.sqlite"
    monkeypatch.setenv("ENSEMBLE_PERSISTENCE_ROOT", str(root))
    monkeypatch.delenv("ENSEMBLE_DATABASE_URL", raising=False)
    url = f"sqlite+aiosqlite:///{db}"
    settings = settings_from_env(url)
    assert settings.database_url == url
    assert db.parent.is_dir()
    assert settings.persistence_root == root.resolve()

def test_system_config_endpoint_exposes_listener_without_private_storage_details(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from ensemble.api import create_app
    from ensemble.store import InMemoryDocumentStore

    monkeypatch.setenv("ENSEMBLE_PERSISTENCE_ROOT", str(tmp_path / "private-persist"))
    monkeypatch.setenv("ENSEMBLE_HOST", "127.0.0.1")
    monkeypatch.setenv("ENSEMBLE_PORT", "9123")
    with TestClient(create_app(store=InMemoryDocumentStore())) as http:
        result = http.get("/api/v1/system/config")
    assert result.status_code == 200
    body = result.json()
    assert body["listener"] == {"host": "127.0.0.1", "port": 9123}
    assert body["persistence"] == {"mode": "durable", "configured": True}
    assert "database_url" not in body
    assert "persistence_root" not in body
    assert str(tmp_path) not in result.text
