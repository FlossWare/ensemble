from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.store import InMemoryDocumentStore
from ensemble.vector_memory import InMemoryVectorStore


def test_vector_query_rejects_embedding_model_mismatch():
    with TestClient(create_app(
        store=InMemoryDocumentStore(), vector_store=InMemoryVectorStore(),
    )) as client:
        stored = client.put("/api/v1/vector/collections/test-vectors/records", json={
            "ids": ["doc-1"],
            "embeddings": [[1.0, 0.0]],
            "documents": ["a document"],
            "metadatas": [{"source_id": "source-1"}],
            "model": "embedding-model-a",
            "dimensions": 2,
        })
        assert stored.status_code == 200

        mismatch = client.post("/api/v1/vector/collections/test-vectors/query", json={
            "embedding": [1.0, 0.0],
            "dimensions": 2,
            "model": "embedding-model-b",
            "top_k": 5,
        })
        assert mismatch.status_code == 409
        assert "model does not match" in mismatch.json()["detail"]

        matched = client.post("/api/v1/vector/collections/test-vectors/query", json={
            "embedding": [1.0, 0.0],
            "dimensions": 2,
            "model": "embedding-model-a",
            "top_k": 5,
        })
        assert matched.status_code == 200
        assert matched.json()["results"][0]["id"] == "doc-1"



def test_poc_vector_route_aliases_preserve_collection_contract():
    with TestClient(create_app(
        store=InMemoryDocumentStore(), vector_store=InMemoryVectorStore(),
    )) as client:
        upsert = client.post("/api/v1/vector/collections/poc-vectors/upsert", json={
            "ids": ["poc-1"],
            "embeddings": [[1.0, 0.0]],
            "documents": ["POC route document"],
            "metadatas": [{"source_id": "poc-source", "project": "test"}],
            "model": "poc-model",
            "dimensions": 2,
        })
        assert upsert.status_code == 200
        assert upsert.json()["upserted"] == 1

        existing = client.post("/api/v1/vector/collections/poc-vectors/existing", json={
            "ids": ["poc-1", "missing"],
        })
        assert existing.status_code == 200
        assert existing.json()["found"] == ["poc-1"]

        query = client.post("/api/v1/vector/collections/poc-vectors/query", json={
            "embedding": [1.0, 0.0],
            "model": "poc-model",
            "dimensions": 2,
            "top_k": 1,
        })
        assert query.status_code == 200
        assert query.json()["results"][0]["id"] == "poc-1"

        metadata = client.post("/api/v1/vector/collections/poc-vectors/metadata", json={
            "ids": ["poc-1"],
            "metadatas": [{"source_id": "poc-source", "project": "renamed"}],
        })
        assert metadata.status_code == 200
        assert metadata.json()["updated"] == 1


def test_vector_health_reports_the_active_backend(tmp_path):
    from ensemble.chroma_vector_store import ChromaVectorStore

    with TestClient(create_app(
        store=InMemoryDocumentStore(),
        vector_store=ChromaVectorStore(tmp_path),
    )) as client:
        health = client.get("/api/v1/vector/health")
        ready = client.get("/api/v1/vector/ready")
        assert health.status_code == 200
        assert health.json()["backend"] == "chromadb"
        assert ready.status_code == 200
        assert ready.json()["backend"] == "chromadb"
