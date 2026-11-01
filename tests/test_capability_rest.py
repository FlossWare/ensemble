from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from ensemble.api import create_app


def test_capability_rest_contracts_are_exposed_and_used(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'ensemble.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    with TestClient(create_app(database_url=database_url)) as client:
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/v1/capabilities/chunking/split" in paths
        assert "/api/v1/capabilities/embeddings" in paths
        assert "/api/v1/capabilities/index/documents" in paths
        assert "/api/v1/capabilities/index/search-candidates" in paths

        split = client.post(
            "/api/v1/capabilities/chunking/split",
            json={"text": "rest capability test"},
        )
        assert split.status_code == 200
        assert split.json()["chunks"] == ["rest capability test"]

        embedded = client.post(
            "/api/v1/capabilities/embeddings",
            json={"texts": ["rest capability test"]},
        )
        assert embedded.status_code == 200
        assert embedded.json()["available"] is False
        assert embedded.json()["embeddings"] == [[]]

        ingested = client.post("/api/v1/documents", json={
            "source_id": "rest-boundary",
            "revision": 1,
            "title": "REST boundary",
            "content": "The index capability stores this REST boundary document.",
        })
        assert ingested.status_code == 201

        indexed = client.post(
            "/api/v1/capabilities/index/search-candidates",
            json={"query": "REST boundary", "limit": 100},
        )
        assert indexed.status_code == 200
        assert indexed.json()[0]["document"]["source_id"] == "rest-boundary"
