import hashlib
import json
import math
import re
from pathlib import Path

from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.retrieval_eval import evaluate_runs
from ensemble.store import InMemoryDocumentStore
from ensemble.vector_memory import InMemoryVectorStore


class DeterministicHashEmbeddings:
    available = True
    dimensions = 128
    model = "retrieval-smoke-hash-v1"

    def status(self):
        return {
            "ok": True, "ready": True,
            "model": self.model, "dimensions": self.dimensions,
        }

    async def embed(self, texts, mode="document"):
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in re.findall(r"[a-z0-9]+", text.casefold()):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimensions
                vector[index] += 1.0 if digest[4] & 1 else -1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


def test_synthetic_retrieval_smoke_fixture_exercises_real_hybrid_search():
    fixture_path = Path(__file__).parents[1] / "eval" / "retrieval-smoke.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture["kind"] == "synthetic-retrieval-smoke"

    app = create_app(
        store=InMemoryDocumentStore(),
        vector_store=InMemoryVectorStore(),
        embedding_provider=DeterministicHashEmbeddings(),
    )
    with TestClient(app) as client:
        for document in fixture["documents"]:
            response = client.post("/api/v1/ingest/documents", json={
                "source_id": document["source_id"],
                "revision": 1,
                "title": document["title"],
                "content": document["content"],
            })
            assert response.status_code == 201, response.text

        qrels = {}
        runs = {}
        for query in fixture["queries"]:
            qrels[query["id"]] = query["relevance"]
            response = client.post("/api/v1/search/hybrid", json={
                "query": query["text"],
            })
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["results"], query["id"]
            ranked_sources = []
            for result in body["results"]:
                source_id = result["source_id"]
                if source_id not in ranked_sources:
                    ranked_sources.append(source_id)
            runs[query["id"]] = ranked_sources

        evaluation = evaluate_runs(
            qrels, {"hybrid": runs}, k=fixture.get("k", 3),
        )
        metrics = evaluation["systems"]["hybrid"]["macro"]
        assert metrics["recall@3"] >= 0.8
        assert metrics["mrr@3"] >= 0.8
        assert metrics["ndcg@3"] >= 0.8
