from fastapi.testclient import TestClient
from ensemble.api import create_app


def test_health_and_ready():
    client = TestClient(create_app())
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json()["status"] == "ready"


def test_ingest_then_lexical_search_returns_provenance():
    client = TestClient(create_app())
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


def test_validation_errors_use_problem_details():
    client = TestClient(create_app())
    response = client.post("/api/v1/documents", json={})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 422
    assert body["code"] == "request-validation-failed"
    assert body["correlationId"]
