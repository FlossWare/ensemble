import asyncio

from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.embeddings import (
    DisabledEmbeddingProvider,
    NomicEmbeddingProvider,
)
from ensemble.store import InMemoryDocumentStore


class FakeArray:
    def __init__(self, rows):
        self.rows = rows

    def tolist(self):
        return self.rows


class FakeNomicEmbedder:
    backend = "onnxruntime-cpu"
    runtime = "onnxruntime/test"
    device = "CPUExecutionProvider"
    precision = "FP32"
    graph_precision = "FP32"
    inference_precision_hint = None
    execution_devices = ["CPUExecutionProvider"]

    def __init__(self):
        self.document_calls = []
        self.query_calls = []

    def embed_documents(self, texts):
        self.document_calls.append(texts)
        return FakeArray([[1.0, 0.0] for _ in texts])

    def embed_queries(self, texts):
        self.query_calls.append(texts)
        return FakeArray([[0.0, 1.0] for _ in texts])


class StubEmbeddingProvider:
    available = True

    def status(self):
        return {
            "ok": True,
            "ready": True,
            "model": "test-embedding-model",
            "dimensions": 2,
        }

    async def embed(self, texts, mode="document"):
        value = 1.0 if mode == "document" else 0.0
        return [[value, 1.0 - value] for _ in texts]


def test_nomic_provider_preserves_document_and_query_modes():
    embedder = FakeNomicEmbedder()
    provider = NomicEmbeddingProvider(embedder)
    documents = asyncio.run(provider.embed(["document text"], mode="document"))
    queries = asyncio.run(provider.embed(["query text"], mode="query"))
    assert documents == [[1.0, 0.0]]
    assert queries == [[0.0, 1.0]]
    assert embedder.document_calls == [["document text"]]
    assert embedder.query_calls == [["query text"]]
    status = provider.status()
    assert status["model"] == "nomic-ai/nomic-embed-text-v1.5"
    assert status["revision"] == "e9b6763023c676ca8431644204f50c2b100d9aab"
    assert status["backend"] == "onnxruntime-cpu"


def test_disabled_embedding_provider_is_explicitly_unavailable():
    provider = DisabledEmbeddingProvider()
    assert provider.available is False
    assert provider.status()["ready"] is False
    assert asyncio.run(provider.embed(["text"])) == [[]]


def test_embedding_rest_contract_and_query_document_modes():
    app = create_app(
        store=InMemoryDocumentStore(),
        embedding_provider=StubEmbeddingProvider(),
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/embeddings/health").status_code == 200
        model = client.get("/api/v1/embeddings/model").json()
        assert model["model"] == "test-embedding-model"
        response = client.post(
            "/api/v1/embeddings/embed",
            json={"texts": ["one", "two"], "mode": "query"},
        )
        assert response.status_code == 200
        assert response.json()["dimensions"] == 2
        assert response.json()["embeddings"] == [[0.0, 1.0], [0.0, 1.0]]


def test_embedding_rest_fails_closed_when_not_configured():
    app = create_app(
        store=InMemoryDocumentStore(),
        embedding_provider=DisabledEmbeddingProvider(),
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/embeddings/health").status_code == 503
        response = client.post(
            "/api/v1/embeddings/embed",
            json={"texts": ["text"], "mode": "document"},
        )
        assert response.status_code == 503
