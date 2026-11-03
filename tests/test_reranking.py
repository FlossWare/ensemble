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


from uuid import uuid4

from ensemble.models import SearchHit
from ensemble.reranking import RerankingDecorator


class AsyncRankingProvider:
    available = True

    def status(self):
        return {"provider": "test-http"}

    async def score(self, query, documents):
        return [float(index) for index, _ in enumerate(documents)]


def _hit(text):
    return SearchHit(
        document_id=uuid4(), source_id="source", revision=1,
        title=text, chunk_id=uuid4(), chunk_ordinal=0, text=text,
        lexical_score=1.0, vector_score=0.0, fusion_score=1.0,
    )


@pytest.mark.asyncio
async def test_reranking_decorator_wraps_retriever_and_preserves_candidate_identity():
    original = [_hit("first"), _hit("second")]

    async def retrieve(query, limit=10, *, rerank=None):
        return {
            "ok": True, "status": "ok", "results": list(original),
            "degraded": [], "reranked": False,
        }

    decorated = RerankingDecorator(AsyncRankingProvider())(retrieve)
    result = await decorated("query", rerank=True)

    assert result["ranking"]["status"] == "reranked"
    assert result["ranking"]["provider"] == "test-http"
    assert result["reranked"] is True
    assert [hit.text for hit in result["results"]] == ["second", "first"]
    assert {hit.chunk_id for hit in result["results"]} == {hit.chunk_id for hit in original}


@pytest.mark.asyncio
async def test_reranking_decorator_falls_back_without_changing_baseline():
    original = [_hit("first"), _hit("second")]

    async def retrieve(query, limit=10, *, rerank=None):
        return {
            "ok": True, "status": "ok", "results": list(original),
            "degraded": [], "reranked": False,
        }

    decorated = RerankingDecorator(None)(retrieve)
    result = await decorated("query", rerank=True)

    assert result["ranking"]["status"] == "fallback"
    assert result["ranking"]["reason"] == "provider_unavailable"
    assert result["reranked"] is False
    assert [hit.chunk_id for hit in result["results"]] == [hit.chunk_id for hit in original]
    assert "reranking" in result["degraded"]
