"""Optional local cross-encoder reranking capability."""

from __future__ import annotations

import os
import asyncio
import math
import json
import httpx
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
    """Decorate any async retriever returning a search-result status mapping."""

    def __init__(self, provider: RankingProvider | None):
        self.provider = provider

    async def apply(self, query: str, result: dict, *, enabled: bool) -> dict:
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
            documents = [item.text[:MAX_DOCUMENT_CHARS] for item in candidates]
            validate_rerank_request(query, documents)
            scores = await provider.score(query, documents)
            if (
                not isinstance(scores, list)
                or len(scores) != len(candidates)
                or any(isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) for score in scores)
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

    def __call__(self, retrieve):
        from functools import wraps

        @wraps(retrieve)
        async def decorated(query: str, *args, **kwargs):
            requested = kwargs.get("rerank")
            enabled = (
                os.getenv("ENSEMBLE_RERANK_ENABLED", "0").strip() == "1"
                if requested is None else bool(requested)
            )
            call_kwargs = dict(kwargs)
            call_kwargs["rerank"] = False
            result = await retrieve(query, *args, **call_kwargs)
            return await self.apply(query, result, enabled=enabled)

        return decorated


MAX_RANKING_REQUEST_BYTES = 256 * 1024
MAX_RANKING_RESPONSE_BYTES = 16 * 1024


class HttpRankingProvider:
    """Opt-in adapter for a remote ranking endpoint returning ordered scores."""

    def __init__(
        self, base_url: str, *, model: str = "", api_key: str = "",
        timeout: float = 10.0, transport: Any | None = None,
    ):
        self.url = base_url.rstrip("/") + "/rerank"
        self.model = model
        self.api_key = api_key
        self.timeout = max(1.0, min(float(timeout), 30.0))
        self.transport = transport

    @property
    def available(self) -> bool:
        return True

    def status(self) -> dict[str, Any]:
        return {
            "ok": True, "ready": True, "provider": "http",
            "model": self.model or None, "runtime": "remote-http",
            "endpoint": self.url, "health_check": "configuration_only",
        }

    async def score(self, query: str, documents: list[str]) -> list[float]:
        validate_rerank_request(query, documents)
        payload: dict[str, Any] = {"query": query, "documents": documents}
        if self.model:
            payload["model"] = self.model
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_RANKING_REQUEST_BYTES:
            raise ValueError("ranking request exceeds 256 KiB")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        timeout = httpx.Timeout(
            connect=min(2.0, self.timeout), read=self.timeout,
            write=min(5.0, self.timeout), pool=min(2.0, self.timeout),
        )
        async with httpx.AsyncClient(
            timeout=timeout, transport=self.transport
        ) as client:
            async with asyncio.timeout(self.timeout):
                async with client.stream(
                    "POST", self.url, headers=headers, content=body
                ) as response:
                    response.raise_for_status()
                    chunks, received = [], 0
                    async for chunk in response.aiter_bytes():
                        received += len(chunk)
                        if received > MAX_RANKING_RESPONSE_BYTES:
                            raise ValueError("ranking response exceeds 16 KiB")
                        chunks.append(chunk)
        try:
            result = json.loads(b"".join(chunks))
            scores = result["scores"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("ranking response must contain a scores array") from exc
        if not isinstance(scores, list) or len(scores) != len(documents):
            raise ValueError("ranking response score count does not match candidate count")
        normalized = []
        for score in scores:
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise ValueError("ranking scores must be finite numbers")
            if not math.isfinite(score):
                raise ValueError("ranking scores must be finite numbers")
            normalized.append(float(score))
        return normalized


def configured_ranking_provider(reranker: Reranker | None) -> RankingProvider | None:
    """Prefer an explicitly configured HTTP provider; otherwise adapt the local model."""
    base_url = os.getenv("ENSEMBLE_RANKING_BASE_URL", "").strip()
    if base_url:
        return HttpRankingProvider(
            base_url,
            model=os.getenv("ENSEMBLE_RANKING_MODEL", "").strip(),
            api_key=os.getenv("ENSEMBLE_RANKING_API_KEY", ""),
            timeout=float(os.getenv("ENSEMBLE_RANKING_TIMEOUT", "10")),
        )
    return LocalRankingProvider(reranker) if reranker is not None else None
