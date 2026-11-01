from __future__ import annotations

import os
import httpx
from typing import Protocol


class EmbeddingProvider(Protocol):
    @property
    def available(self) -> bool: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class DisabledEmbeddingProvider:
    available = False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]


class OpenAICompatibleEmbeddingProvider:
    """Adapter for OpenAI-compatible /embeddings endpoints."""

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 60.0):
        self.url = base_url.rstrip("/") + "/embeddings"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return True

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.url, headers=headers, json={"model": self.model, "input": texts}
            )
            response.raise_for_status()
            payload = response.json()
        vectors = [
            [float(value) for value in item["embedding"]]
            for item in sorted(payload["data"], key=lambda item: item.get("index", 0))
        ]
        if len(vectors) != len(texts):
            raise RuntimeError(f"embedding provider returned {len(vectors)} vectors for {len(texts)} inputs")
        return vectors


def configured_embedding_provider() -> EmbeddingProvider:
    base_url = os.getenv("ENSEMBLE_EMBEDDING_BASE_URL", "").strip()
    model = os.getenv("ENSEMBLE_EMBEDDING_MODEL", "").strip()
    if not base_url or not model:
        return DisabledEmbeddingProvider()
    return OpenAICompatibleEmbeddingProvider(
        base_url=base_url,
        model=model,
        api_key=os.getenv("ENSEMBLE_EMBEDDING_API_KEY", ""),
        timeout=float(os.getenv("ENSEMBLE_EMBEDDING_TIMEOUT", "60")),
    )
