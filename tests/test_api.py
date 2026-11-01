import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from ensemble.api import create_app


def _client(tmp_path):
    database = tmp_path / "ensemble.db"
    database_url = f"sqlite+aiosqlite:///{database}"
    migration_config = Config("alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")
    return TestClient(create_app(database_url=database_url))


def test_startup_rejects_unmigrated_database(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'unmigrated.db'}"
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        with TestClient(create_app(database_url=database_url)):
            pass


def test_health_and_ready(tmp_path):
    with _client(tmp_path) as client:
        assert client.get("/health").json()["status"] == "ok"
        ready = client.get("/ready").json()
        assert ready["status"] == "ready"
        assert ready["persistence"] == "durable"


def test_ingest_then_lexical_search_returns_provenance(tmp_path):
    with _client(tmp_path) as client:
        response = client.post("/api/v1/documents", json={
            "source_id": "architecture-note",
            "revision": 1,
            "title": "Vector retrieval",
            "content": "Vector retrieval combines embeddings with lexical search for better recall."
        })
        assert response.status_code == 201
        document = response.json()
        assert document["chunk_count"] == 1
        assert document["embedded"] is False

        result = client.post("/api/v1/search", json={"query": "lexical embeddings", "limit": 5})
        assert result.status_code == 200
        hits = result.json()
        assert len(hits) == 1
        assert hits[0]["source_id"] == "architecture-note"
        assert hits[0]["document_id"] == document["id"]
        assert hits[0]["lexical_score"] > 0


def test_validation_errors_use_problem_details(tmp_path):
    with _client(tmp_path) as client:
        response = client.post("/api/v1/documents", json={})
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["status"] == 422
        assert body["code"] == "request-validation-failed"
        assert body["correlationId"]


def test_documents_survive_application_restart(tmp_path):
    payload = {
        "source_id": "persistent-note",
        "revision": 1,
        "title": "Durable storage",
        "content": "Durable storage must survive application restarts and process replacement.",
    }
    with _client(tmp_path) as client:
        first = client.post("/api/v1/documents", json=payload).json()

    with _client(tmp_path) as client:
        hits = client.post("/api/v1/search", json={"query": "durable storage"}).json()
        assert len(hits) == 1
        assert hits[0]["document_id"] == first["id"]


def test_reingest_replaces_chunk_content_for_same_revision(tmp_path):
    with _client(tmp_path) as client:
        original = {
            "source_id": "mutable-source",
            "revision": 1,
            "title": "Mutable source",
            "content": "The original content contains obsolete terminology.",
        }
        first = client.post("/api/v1/documents", json=original).json()
        replacement = {
            **original,
            "content": "The replacement content contains current terminology.",
        }
        second = client.post("/api/v1/documents", json=replacement).json()
        assert second["id"] == first["id"]

        old_hits = client.post("/api/v1/search", json={"query": "obsolete"}).json()
        new_hits = client.post("/api/v1/search", json={"query": "current terminology"}).json()
        assert old_hits == []
        assert len(new_hits) == 1
        assert new_hits[0]["text"] == replacement["content"]


def test_reingest_is_idempotent_and_latest_revision_is_searchable(tmp_path):
    with _client(tmp_path) as client:
        original = {
            "source_id": "versioned-note",
            "revision": 1,
            "title": "First revision",
            "content": "The original document discusses obsolete indexing behavior.",
        }
        first = client.post("/api/v1/documents", json=original).json()
        again = client.post("/api/v1/documents", json=original).json()
        assert again["id"] == first["id"]

        revised = {
            "source_id": "versioned-note",
            "revision": 2,
            "title": "Second revision",
            "content": "The current document discusses durable indexing behavior.",
        }
        second = client.post("/api/v1/documents", json=revised).json()
        assert second["id"] != first["id"]

        current = client.post("/api/v1/search", json={"query": "indexing behavior"}).json()
        assert len(current) == 1
        assert current[0]["revision"] == 2
        assert current[0]["document_id"] == second["id"]

        historical = client.post("/api/v1/documents", json=original).json()
        assert historical["id"] == first["id"]
        still_current = client.post("/api/v1/search", json={"query": "indexing behavior"}).json()
        assert len(still_current) == 1
        assert still_current[0]["revision"] == 2
