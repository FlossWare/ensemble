from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from ensemble.api import create_app


def _client(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'generation-api.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return TestClient(create_app(database_url=database_url))


def test_generation_api_creates_build_and_reports_missing_capabilities(tmp_path):
    with _client(tmp_path) as client:
        created = client.post("/api/v1/index/generations", json={})
        assert created.status_code == 200
        generation_id = created.json()["generation"]["generation_id"]

        status = client.get("/api/v1/index/generations")
        assert status.status_code == 200
        build = status.json()["build"]
        assert build["generation_id"] == generation_id
        assert build["activation_ready"] is False
        assert "memory" in build["blocked_capabilities"]
        assert "memory" in build["missing_checkpoints"]
        assert "memory_reconciliation" in build["missing_checkpoints"]


def test_generation_api_runs_bounded_index_copy_and_fails_closed_on_fence(tmp_path):
    with _client(tmp_path) as client:
        created = client.post(
            "/api/v1/index/generations",
            json={"generation_id": "generation-api-test"},
        )
        assert created.status_code == 200

        copied = client.post(
            "/api/v1/index/generations/generation-api-test/copy/index",
            json={"page_size": 10},
        )
        assert copied.status_code == 200
        assert copied.json()["status"] == "complete"
        assert copied.json()["page_count"] == 0

        fenced = client.post(
            "/api/v1/index/generations/generation-api-test/fence",
        )
        assert fenced.status_code == 409
        assert "memory" in fenced.json()["detail"]
