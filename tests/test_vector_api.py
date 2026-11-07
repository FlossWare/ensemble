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
