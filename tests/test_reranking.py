import pytest
from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.reranking import (
    CrossEncoderReranker,
    DisabledReranker,
    RerankingUnavailable,
    validate_rerank_request,
)
from ensemble.store import InMemoryDocumentStore


class StubReranker:
    available = True

    def status(self):
        return {
            "ok": True,
            "ready": True,
            "model_loaded": True,
            "model": "stub-cross-encoder",
        }

    def score(self, query, documents):
        validate_rerank_request(query, documents)
        return [float(len(document)) for document in documents]


def test_rerank_scores_align_with_input_order():
    reranker = StubReranker()
    scores = reranker.score("query", ["short", "a longer document"])
    assert scores == [5.0, 17.0]


def test_rerank_request_validation():
    validate_rerank_request("query", ["candidate"])
    with pytest.raises(ValueError):
        validate_rerank_request("", ["candidate"])
    with pytest.raises(ValueError):
        validate_rerank_request("query", [])
    with pytest.raises(ValueError):
        validate_rerank_request("query", ["x"] * 51)
    with pytest.raises(ValueError):
        validate_rerank_request("query", ["x" * 4_001])


def test_missing_local_model_fails_closed(tmp_path):
    reranker = CrossEncoderReranker(str(tmp_path / "missing"))
    assert reranker.available is False
    assert "model directory not found" in reranker.status()["error"]
    with pytest.raises(RerankingUnavailable):
        reranker.score("query", ["candidate"])


def test_disabled_reranker_reports_unavailable():
    reranker = DisabledReranker()
    assert reranker.available is False
    with pytest.raises(RerankingUnavailable):
        reranker.score("query", ["candidate"])


def test_reranking_rest_contract_with_stub_provider():
    app = create_app(store=InMemoryDocumentStore(), reranker=StubReranker())
    with TestClient(app) as client:
        assert client.get("/api/v1/reranking/health").status_code == 200
        response = client.post(
            "/api/v1/reranking/rerank",
            json={"query": "search relevance", "documents": ["short", "longer candidate"]},
        )
        assert response.status_code == 200
        assert response.json()["scores"] == [5.0, 16.0]


def test_reranking_rest_reports_missing_provider():
    app = create_app(store=InMemoryDocumentStore(), reranker=DisabledReranker())
    with TestClient(app) as client:
        assert client.get("/api/v1/reranking/health").status_code == 503
        response = client.post(
            "/api/v1/reranking/rerank",
            json={"query": "search relevance", "documents": ["candidate"]},
        )
        assert response.status_code == 503
