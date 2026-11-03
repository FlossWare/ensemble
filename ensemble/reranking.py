"""Optional local cross-encoder reranking capability."""

from __future__ import annotations

import os
import math
import threading
from pathlib import Path
from typing import Any, Protocol


MODEL_ID = "cross-encoder/ms-marco-MiniLM-L6-v2 (local)"
MODEL_REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"
DEFAULT_MODEL_DIR = (
    "~/.FlossWare/models/rerankers/cross-encoder/"
    "ms-marco-MiniLM-L6-v2/233902d25c440f23af6f7d6e94d2946bac0bee0a"
)
MAX_DOCUMENTS = 50
MAX_DOCUMENT_CHARS = 4_000
MAX_QUERY_CHARS = 2_000


class RerankingUnavailable(RuntimeError):
    pass


class Reranker(Protocol):
    @property
    def available(self) -> bool: ...

    def status(self) -> dict[str, Any]: ...

    def score(self, query: str, documents: list[str]) -> list[float]: ...


class DisabledReranker:
    @property
    def available(self) -> bool:
        return False

    def status(self) -> dict[str, Any]:
        return {
            "ok": False,
            "ready": False,
            "model_loaded": False,
            "model": MODEL_ID,
            "error": "reranking provider is not configured",
        }

    def score(self, query: str, documents: list[str]) -> list[float]:
        raise RerankingUnavailable("reranking provider is not configured")


class CrossEncoderReranker:
    """Load the pinned local POC cross-encoder; never download at runtime."""

    def __init__(self, model_dir: str | None = None):
        self.model_dir = Path(
            os.path.expanduser(model_dir or os.environ.get("ENSEMBLE_RERANK_MODEL_DIR", DEFAULT_MODEL_DIR))
        )
        self.model = None
        self.load_error: str | None = None
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.model_dir.is_dir():
            self.load_error = f"model directory not found: {self.model_dir}"
            return
        try:
            from sentence_transformers import CrossEncoder
            self.model = CrossEncoder(str(self.model_dir), device="cpu")
            self.load_error = None
        except Exception as exc:
            self.model = None
            self.load_error = str(exc)

    @property
    def available(self) -> bool:
        return self.model is not None

    def status(self) -> dict[str, Any]:
        ready = self.available
        return {
            "ok": ready,
            "ready": ready,
            "model_loaded": ready,
            "model": MODEL_ID,
            "revision": MODEL_REVISION,
            "runtime": "sentence-transformers/cpu",
            "error": self.load_error,
        }

    def score(self, query: str, documents: list[str]) -> list[float]:
        validate_rerank_request(query, documents)
        if self.model is None:
            raise RerankingUnavailable(f"model not loaded: {self.load_error}")
        with self._lock:
            scores = self.model.predict([(query, document) for document in documents])
        return [float(score) for score in scores]


def validate_rerank_request(query: Any, documents: Any) -> None:
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
        raise ValueError(
            f"query must be a non-empty string of at most {MAX_QUERY_CHARS} characters"
        )
    if not isinstance(documents, list) or not documents or len(documents) > MAX_DOCUMENTS:
        raise ValueError(f"documents must be a list of 1 to {MAX_DOCUMENTS} strings")
    for document in documents:
        if not isinstance(document, str) or len(document) > MAX_DOCUMENT_CHARS:
            raise ValueError(
                f"each document must be a string of at most {MAX_DOCUMENT_CHARS} characters"
            )


def configured_reranker() -> Reranker:
    return CrossEncoderReranker()


class RankingProvider(Protocol):
    """Async scoring contract shared by local and remote ranking providers."""

    @property
    def available(self) -> bool: ...

    def status(self) -> dict[str, Any]: ...

    async def score(self, query: str, documents: list[str]) -> list[float]: ...


class LocalRankingProvider:
    """Adapt the existing synchronous cross-encoder to the async provider contract."""

    def __init__(self, reranker: Reranker):
        self.reranker = reranker

    @property
    def available(self) -> bool:
        return self.reranker.available

    def status(self) -> dict[str, Any]:
        return {**self.reranker.status(), "provider": "local-cross-encoder"}

    async def score(self, query: str, documents: list[str]) -> list[float]:
        import asyncio
        return await asyncio.to_thread(self.reranker.score, query, documents)


class RerankingDecorator:
    """Decorate any async retriever returning a search-result status mapping.

    The wrapped retriever remains authoritative for candidate identity, provenance,
    channel diagnostics, and baseline order. Ranking only changes candidate order.
    """

    def __init__(self, provider: RankingProvider | None):
        self.provider = provider

    def __call__(self, retrieve):
        from functools import wraps

        @wraps(retrieve)
        async def decorated(query: str, *args, **kwargs):
            requested = kwargs.get("rerank")
            enabled = (
                os.getenv("ENSEMBLE_RERANK_ENABLED", "0").strip() == "1"
                if requested is None else bool(requested)
            )
            # The base retriever owns retrieval and RRF. It must not rerank itself.
            call_kwargs = dict(kwargs)
            call_kwargs["rerank"] = False
            result = await retrieve(query, *args, **call_kwargs)
            result["ranking"] = {
                "status": "not_requested",
                "provider": None,
                "candidate_count": 0,
            }
            result["reranked"] = False
            if not enabled or not result.get("results"):
                return result
            provider = self.provider
            candidates = result["results"][:min(MAX_DOCUMENTS, len(result["results"]))]
            result["ranking"]["candidate_count"] = len(candidates)
            if provider is None or not provider.available:
                result["ranking"].update({
                    "status": "fallback",
                    "provider": None if provider is None else provider.status().get("provider"),
                    "reason": "provider_unavailable",
                })
                result.setdefault("degraded", []).append("reranking")
                result["degraded"] = sorted(set(result["degraded"]))
                result["status"] = "partial" if result.get("ok") else result.get("status", "unavailable")
                return result
            try:
                validate_rerank_request(query, [item.text[:MAX_DOCUMENT_CHARS] for item in candidates])
                scores = await provider.score(
                    query, [item.text[:MAX_DOCUMENT_CHARS] for item in candidates]
                )
                if (
                    not isinstance(scores, list)
                    or len(scores) != len(candidates)
                    or any(isinstance(score, bool) or not math.isfinite(float(score)) for score in scores)
                ):
                    raise RuntimeError("ranking provider returned invalid or misaligned scores")
                scored = [
                    candidate.model_copy(update={"rerank_score": float(score)})
                    for candidate, score in zip(candidates, scores)
                ]
                result["results"] = [
                    item for _, item in sorted(
                        enumerate(scored), key=lambda pair: (-pair[1].rerank_score, pair[0])
                    )
                ] + result["results"][len(candidates):]
                result["ranking"].update({
                    "status": "reranked",
                    "provider": provider.status().get("provider", "unknown"),
                })
                result["reranked"] = True
            except Exception as exc:
                result["ranking"].update({
                    "status": "fallback",
                    "provider": provider.status().get("provider", "unknown"),
                    "reason": "provider_error",
                    "error": type(exc).__name__,
                })
                result.setdefault("degraded", []).append("reranking")
                result["degraded"] = sorted(set(result["degraded"]))
                result["status"] = "partial" if result.get("ok") else result.get("status", "unavailable")
            return result

        return decorated
