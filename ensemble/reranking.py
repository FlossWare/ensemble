from __future__ import annotations

import os
import httpx
from typing import Protocol


class Reranker(Protocol):
    @property
    def available(self) -> bool: ...

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[dict]: ...


class DisabledReranker:
    available = False

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[dict]:
        raise RuntimeError("reranking provider is not configured")


class CohereCompatibleReranker:
    """Adapter for Cohere-compatible /rerank APIs."""

    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 60.0):
        self.url = base_url.rstrip("/") + "/rerank"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return True

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[dict]:
        if not documents:
            return []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model, "query": query,
                    "documents": documents, "top_n": min(top_n, len(documents)),
                },
            )
            response.raise_for_status()
            payload = response.json()
        results = []
        for item in payload.get("results", []):
            index = int(item["index"])
            if index < 0 or index >= len(documents):
                raise RuntimeError("reranker returned an out-of-range document index")
            results.append({
                "index": index,
                "score": float(item["relevance_score"]),
                "text": documents[index],
            })
        return results


def configured_reranker() -> Reranker:
    base_url = os.getenv("ENSEMBLE_RERANK_BASE_URL", "").strip()
    model = os.getenv("ENSEMBLE_RERANK_MODEL", "").strip()
    if not base_url or not model:
        return DisabledReranker()
    return CohereCompatibleReranker(
        base_url, model, os.getenv("ENSEMBLE_RERANK_API_KEY", ""),
        float(os.getenv("ENSEMBLE_RERANK_TIMEOUT", "60")),
    )
