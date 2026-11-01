from __future__ import annotations

import asyncio
import os
import httpx
from typing import Protocol, Any


MODEL_ID = "nomic-ai/nomic-embed-text-v1.5"
MODEL_REVISION = "e9b6763023c676ca8431644204f50c2b100d9aab"
DIMENSIONS = 768
MAX_TEXTS = 64
MAX_TEXT_CHARS = 65_536


class EmbeddingProvider(Protocol):
    @property
    def available(self) -> bool: ...

    def status(self) -> dict[str, Any]: ...

    async def embed(self, texts: list[str], mode: str = "document") -> list[list[float]]: ...


class DisabledEmbeddingProvider:
    available = False

    def status(self) -> dict[str, Any]:
        return {
            "ok": False,
            "ready": False,
            "model": None,
            "error": "embedding provider is not configured",
        }

    async def embed(self, texts: list[str], mode: str = "document") -> list[list[float]]:
        return [[] for _ in texts]


class FailedEmbeddingProvider:
    available = False

    def __init__(self, error: str):
        self.error = error

    def status(self) -> dict[str, Any]:
        return {"ok": False, "ready": False, "model": MODEL_ID, "error": self.error}

    async def embed(self, texts: list[str], mode: str = "document") -> list[list[float]]:
        raise RuntimeError(f"embedding provider unavailable: {self.error}")


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

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "ready": True,
            "provider": "openai-compatible",
            "model": self.model,
            "runtime": "remote-http",
        }

    async def embed(self, texts: list[str], mode: str = "document") -> list[list[float]]:
        if mode not in {"document", "query"}:
            raise ValueError("mode must be document or query")
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


class NomicEmbeddingProvider:
    """Pinned local Nomic model with explicit ONNX Runtime/OpenVINO backend selection."""

    def __init__(self, embedder: Any | None = None):
        if embedder is None:
            from .nomic_embed_lib import Embedder
            embedder = Embedder()
        self.embedder = embedder

    @property
    def available(self) -> bool:
        return True

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "ready": True,
            "provider": "local-nomic",
            "model": MODEL_ID,
            "revision": MODEL_REVISION,
            "dimensions": DIMENSIONS,
            "backend": self.embedder.backend,
            "runtime": self.embedder.runtime,
            "device": self.embedder.device,
            "precision": self.embedder.precision,
            "graph_precision": self.embedder.graph_precision,
            "inference_precision_hint": self.embedder.inference_precision_hint,
            "execution_devices": self.embedder.execution_devices,
        }

    async def embed(self, texts: list[str], mode: str = "document") -> list[list[float]]:
        if mode not in {"document", "query"}:
            raise ValueError("mode must be document or query")
        if not texts:
            return []
        encoder = self.embedder.embed_documents if mode == "document" else self.embedder.embed_queries
        vectors = await asyncio.to_thread(encoder, texts)
        return vectors.tolist()


def configured_embedding_provider() -> EmbeddingProvider:
    backend = os.getenv("ENSEMBLE_EMBED_BACKEND", "").strip()
    model_dir = os.getenv("ENSEMBLE_EMBEDDING_MODEL_DIR", "").strip()
    if backend or model_dir:
        try:
            return NomicEmbeddingProvider()
        except Exception as exc:
            return FailedEmbeddingProvider(str(exc))

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
