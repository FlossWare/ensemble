import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from ensemble.api import create_app


@pytest.fixture(autouse=True)
def configure_api_keys(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_API_KEYS", '{"test-key":{"subject":"test-user","partitions":["default"],"permissions":["read","write"]}}')


def test_capability_rest_contracts_are_exposed_and_used(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'ensemble.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    with TestClient(create_app(database_url=database_url), headers={"Authorization": "Bearer test-key", "X-Ensemble-Partition": "default"}) as client:
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



def test_vector_search_capability_returns_nearest_candidates(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'vector.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    with TestClient(create_app(database_url=database_url), headers={"Authorization": "Bearer test-key", "X-Ensemble-Partition": "default"}) as client:
        first = client.post("/api/v1/capabilities/index/documents", json={
            "source_id": "vector-first",
            "revision": 1,
            "title": "First vector",
            "content": "first vector document",
            "chunks": ["first vector document"],
            "embeddings": [[1.0, 0.0]],
        })
        second = client.post("/api/v1/capabilities/index/documents", json={
            "source_id": "vector-second",
            "revision": 1,
            "title": "Second vector",
            "content": "second vector document",
            "chunks": ["second vector document"],
            "embeddings": [[0.0, 1.0]],
        })
        assert first.status_code == 201
        assert second.status_code == 201

        nearest = client.post("/api/v1/capabilities/index/vector-search", json={
            "embedding": [0.9, 0.1],
            "limit": 1,
        })
        assert nearest.status_code == 200
        assert len(nearest.json()) == 1
        assert nearest.json()[0]["document"]["source_id"] == "vector-first"



def test_tokenization_contract_and_disabled_reranker(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'tokenization.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    with TestClient(
        create_app(database_url=database_url),
        headers={"Authorization": "Bearer test-key", "X-Ensemble-Partition": "default"},
    ) as client:
        tokenized = client.post(
            "/api/v1/capabilities/tokenization/tokenize",
            json={"text": "Hello, world!"},
        )
        assert tokenized.status_code == 200
        assert tokenized.json()["tokenizer"] == "ensemble-unicode-v1"
        assert tokenized.json()["tokens"] == ["Hello", ",", "world", "!"]
        assert tokenized.json()["token_count"] == 4

        reranked = client.post(
            "/api/v1/capabilities/reranking/rerank",
            json={"query": "retrieval", "documents": ["semantic retrieval"]},
        )
        assert reranked.status_code == 503
