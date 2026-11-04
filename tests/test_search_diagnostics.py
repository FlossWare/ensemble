from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.store import InMemoryDocumentStore
from ensemble.vector_memory import InMemoryVectorStore
from ensemble.thompson_store import InMemoryThompsonStore


class UnavailableEmbeddings:
    available = False

    def status(self):
        return {"ok": False, "ready": False, "error": "not configured"}

    async def embed(self, texts, mode="document"):
        raise RuntimeError("provider unavailable")


class FixedEmbeddings:
    available = True

    def status(self):
        return {"ok": True, "ready": True, "model": "diagnostic-test", "dimensions": 3}

    async def embed(self, texts, mode="document"):
        return [[1.0, 0.0, 0.0] for _ in texts]


class BrokenLexicalStore(InMemoryDocumentStore):
    async def lexical_search(self, query, limit=10):
        raise OSError("simulated lexical outage")


class BrokenVectorStore(InMemoryVectorStore):
    async def query(self, collection, embedding, top_k=10):
        raise OSError("simulated vector collection outage")


def _ingest(client, source_id="diagnostic-source"):
    response = client.post("/api/v1/ingest/documents", json={
        "source_id": source_id,
        "revision": 1,
        "title": "Retrieval diagnostics",
        "content": "Hybrid retrieval must report independent lexical and vector channel health.",
    })
    assert response.status_code == 201


def test_hybrid_reports_lexical_success_when_embedding_provider_is_unavailable():
    app = create_app(
        store=InMemoryDocumentStore(),
        embedding_provider=UnavailableEmbeddings(),
    )
    with TestClient(app) as client:
        _ingest(client)
        response = client.post("/api/v1/search/hybrid", json={
            "query": "independent lexical retrieval",
        })
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "partial"
        assert body["ok"] is True
        assert body["channels"]["lexical"]["status"] == "ok"
        assert body["channels"]["lexical"]["candidate_count"] > 0
        assert body["channels"]["vector"]["status"] == "unavailable"
        assert body["retrieval"] == "lexical"
        assert "embeddings" in body["degraded"]
        assert body["results"]


def test_hybrid_uses_vector_channel_when_lexical_search_fails():
    app = create_app(
        store=BrokenLexicalStore(),
        embedding_provider=FixedEmbeddings(),
        vector_store=InMemoryVectorStore(),
    )
    with TestClient(app) as client:
        _ingest(client)
        response = client.post("/api/v1/search/hybrid", json={
            "query": "independent lexical retrieval",
        })
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "partial"
        assert body["ok"] is True
        assert body["channels"]["lexical"]["status"] == "unavailable"
        assert body["channels"]["vector"]["status"] == "ok"
        assert body["channels"]["vector"]["document_candidate_count"] > 0
        assert body["retrieval"] == "hybrid"
        assert "lexical" in body["degraded"]
        assert body["results"]


def test_hybrid_reports_collection_failure_without_discarding_document_vectors():
    app = create_app(
        store=InMemoryDocumentStore(),
        embedding_provider=FixedEmbeddings(),
        vector_store=BrokenVectorStore(),
    )
    with TestClient(app) as client:
        _ingest(client)
        response = client.post("/api/v1/search/hybrid", json={
            "query": "independent lexical retrieval",
        })
        assert response.status_code == 200
        body = response.json()
        assert body["channels"]["lexical"]["status"] == "ok"
        assert body["channels"]["vector"]["status"] == "partial"
        assert body["channels"]["vector"]["document_candidate_count"] > 0
        assert body["channels"]["vector"]["collection_candidate_count"] == 0
        assert "vector_collection" in body["degraded"]
        assert body["results"]


def test_hybrid_exposes_deterministic_rrf_contributions_and_configured_k(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_RRF_K", "20")
    app = create_app(
        store=InMemoryDocumentStore(),
        embedding_provider=FixedEmbeddings(),
        vector_store=InMemoryVectorStore(),
    )
    with TestClient(app) as client:
        _ingest(client)
        response = client.post("/api/v1/search/hybrid", json={
            "query": "independent lexical retrieval",
        })
        assert response.status_code == 200
        body = response.json()
        assert body["fusion"]["algorithm"] == "rrf"
        assert body["fusion"]["k"] == 20
        assert body["fusion"]["channels"]["lexical"]["candidate_count"] > 0
        assert body["fusion"]["channels"]["vector"]["candidate_count"] > 0
        diagnostics = body["fusion"]["results"]
        assert diagnostics
        first = diagnostics[0]
        assert first["lexical_rank"] == 1
        assert first["vector_rank"] == 1
        assert first["contributions"]["lexical"] == round(1 / 21, 8)
        assert first["contributions"]["vector"] == round(1 / 21, 8)
        assert first["score"] == round(2 / 21, 8)


def test_hybrid_rrf_uses_stable_identifier_ties():
    from ensemble.services import _rank

    assert _rank({"chunk-b": 1.0, "chunk-a": 1.0}) == {
        "chunk-a": 1,
        "chunk-b": 2,
    }


def test_hybrid_supports_static_weighted_rrf(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_RRF_WEIGHTS", '{"lexical": 2.0, "vector": 0.5}')
    app = create_app(
        store=InMemoryDocumentStore(),
        embedding_provider=FixedEmbeddings(),
        vector_store=InMemoryVectorStore(),
    )
    with TestClient(app) as client:
        _ingest(client)
        response = client.post("/api/v1/search/hybrid", json={
            "query": "independent lexical retrieval",
        })
        assert response.status_code == 200
        fusion = response.json()["fusion"]
        assert fusion["weight_strategy"] == "static"
        assert fusion["weights"] == {"lexical": 2.0, "vector": 0.5}
        assert fusion["algorithm"] == "weighted_rrf"


def test_hybrid_uses_persisted_thompson_channel_feedback(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_RRF_WEIGHT_STRATEGY", "thompson")
    thompson = InMemoryThompsonStore()
    app = create_app(
        store=InMemoryDocumentStore(),
        embedding_provider=FixedEmbeddings(),
        vector_store=InMemoryVectorStore(),
        thompson_store=thompson,
    )
    with TestClient(app) as client:
        _ingest(client)
        for _ in range(8):
            assert client.post("/api/v1/thompson/outcomes", json={
                "model": "retrieval:lexical", "success": True,
            }).status_code == 200
        for _ in range(8):
            assert client.post("/api/v1/thompson/outcomes", json={
                "model": "retrieval:vector", "success": False,
            }).status_code == 200
        response = client.post("/api/v1/search/hybrid", json={
            "query": "independent lexical retrieval",
        })
        assert response.status_code == 200
        fusion = response.json()["fusion"]
        assert fusion["weight_strategy"] == "thompson"
        assert fusion["algorithm"] == "weighted_rrf"
        assert fusion["weights"]["lexical"] > 0
        assert fusion["weights"]["vector"] > 0
