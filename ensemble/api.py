from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .chunking import Chunker, ChunkingError, PROFILES as CHUNKING_PROFILES, chunk as chunk_text, describe_profiles as describe_chunk_profiles
from .embeddings import configured_embedding_provider
from .models import DocumentIn, DocumentOut, SearchIn, SearchHit
from .services import DocumentService, SearchService
from .store import DocumentStore, SqlDocumentStore
from .tokenization import PROFILES as TOKENIZATION_PROFILES, TokenizationError, describe_profiles, tokenize
from .extraction import extract
from .intent import RULES as INTENT_RULES, classify
from .ranking import DEFAULT_K, fuse
from .reranking import MODEL_ID as RERANK_MODEL_ID, Reranker, RerankingUnavailable, configured_reranker, validate_rerank_request


def _problem(request: Request, status: int, title: str, detail: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": "about:blank",
            "title": title,
            "status": status,
            "detail": detail,
            "instance": request.url.path,
            "code": code,
            "correlationId": str(uuid4()),
        },
    )


def create_app(
    database_url: str | None = None,
    store: DocumentStore | None = None,
    reranker: Reranker | None = None,
) -> FastAPI:
    document_store = store or SqlDocumentStore(database_url)
    embeddings = configured_embedding_provider()
    active_reranker = reranker or configured_reranker()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await document_store.initialize()
            yield
        finally:
            await document_store.close()

    app = FastAPI(
        title="Ensemble API",
        version="0.1",
        description="REST-first memory, knowledge, and retrieval platform.",
        lifespan=lifespan,
    )
    documents = DocumentService(document_store, Chunker(), embeddings)
    search = SearchService(document_store, embeddings)

    app.state.store = document_store
    app.state.embedding_provider = embeddings
    app.state.reranker = active_reranker

    @app.exception_handler(HTTPException)
    async def http_problem(request: Request, exc: HTTPException):
        title = "Client Error" if exc.status_code < 500 else "Server Error"
        detail = exc.detail if isinstance(exc.detail, str) else title
        return _problem(request, exc.status_code, title, detail, "http-error")

    @app.exception_handler(RequestValidationError)
    async def validation_problem(request: Request, exc: RequestValidationError):
        return _problem(
            request, 422, "Unprocessable Content",
            "Request validation failed", "request-validation-failed"
        )

    @app.get("/health", tags=["operations"])
    async def health():
        return {"status": "ok", "service": "ensemble"}

    @app.get("/ready", tags=["operations"])
    async def ready():
        try:
            await document_store.check_ready()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="persistence unavailable") from exc
        return {
            "status": "ready",
            "persistence": "durable",
            "embeddings": embeddings.available,
            "capabilities": ["documents", "search", "chunking", "tokenization", "extraction", "intent", "ranking", "reranking"],
        }

    @app.get("/api/v1/chunking/health", tags=["chunking"])
    async def chunking_health():
        return {"ok": True, "service": "chunking"}

    @app.get("/api/v1/chunking/ready", tags=["chunking"])
    async def chunking_ready():
        return {"ok": True, "ready": True, "profiles": sorted(CHUNKING_PROFILES)}

    @app.get("/api/v1/chunking/profiles", tags=["chunking"])
    async def chunking_profiles():
        return {"ok": True, "profiles": describe_chunk_profiles()}

    @app.post("/api/v1/chunking/chunk", tags=["chunking"])
    async def chunk_document(payload: dict[str, Any]):
        profile = payload.get("profile")
        try:
            chunks = chunk_text(
                profile,
                payload.get("source_id"),
                payload.get("text"),
                payload.get("media_type", "text/plain"),
            )
        except ChunkingError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        return {
            "ok": True,
            "profile": {"name": profile, "version": CHUNKING_PROFILES[profile]["version"]},
            "chunks": chunks,
        }

    @app.get("/api/v1/tokenization/health", tags=["tokenization"])
    async def tokenization_health():
        return {"ok": True, "service": "tokenization"}

    @app.get("/api/v1/tokenization/ready", tags=["tokenization"])
    async def tokenization_ready():
        return {"ok": True, "ready": True, "profiles": sorted(TOKENIZATION_PROFILES)}

    @app.get("/api/v1/tokenization/profiles", tags=["tokenization"])
    async def tokenization_profiles():
        return {"ok": True, "profiles": describe_profiles()}

    @app.post("/api/v1/tokenization/tokenize", tags=["tokenization"])
    async def tokenize_text(payload: dict[str, Any]):
        try:
            profile = payload.get("profile")
            tokens = tokenize(profile, payload.get("text"))
        except TokenizationError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        return {
            "ok": True,
            "profile": {"name": profile, "version": TOKENIZATION_PROFILES[profile]["version"]},
            "tokens": tokens,
        }

    @app.get("/api/v1/extraction/health", tags=["extraction"])
    async def extraction_health():
        return {"ok": True, "service": "extraction"}

    @app.get("/api/v1/extraction/ready", tags=["extraction"])
    async def extraction_ready():
        return {"ok": True, "ready": True}

    @app.post("/api/v1/extraction/extract", tags=["extraction"])
    async def extract_entities(payload: dict[str, Any]):
        try:
            entities = extract(payload.get("text"))
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "entities": entities}

    @app.get("/api/v1/intent/health", tags=["intent"])
    async def intent_health():
        return {"ok": True, "service": "intent"}

    @app.get("/api/v1/intent/ready", tags=["intent"])
    async def intent_ready():
        return {"ok": True, "ready": True, "labels": sorted(INTENT_RULES)}

    @app.post("/api/v1/intent/classify", tags=["intent"])
    async def classify_intent(payload: dict[str, Any]):
        try:
            result = classify(payload.get("text"))
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.get("/api/v1/reranking/health", tags=["reranking"])
    async def reranking_health():
        status = active_reranker.status()
        return JSONResponse(status_code=200 if active_reranker.available else 503, content=status)

    @app.get("/api/v1/reranking/ready", tags=["reranking"])
    async def reranking_ready():
        status = active_reranker.status()
        return JSONResponse(status_code=200 if active_reranker.available else 503, content=status)

    @app.post("/api/v1/reranking/rerank", tags=["reranking"])
    async def rerank_candidates(payload: dict[str, Any]):
        query, documents = payload.get("query"), payload.get("documents")
        try:
            validate_rerank_request(query, documents)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            scores = await asyncio.to_thread(active_reranker.score, query, documents)
        except RerankingUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True, "model": RERANK_MODEL_ID, "scores": scores}

    @app.get("/api/v1/ranking/health", tags=["ranking"])
    async def ranking_health():
        return {"ok": True, "service": "ranking"}

    @app.get("/api/v1/ranking/ready", tags=["ranking"])
    async def ranking_ready():
        return {"ok": True, "ready": True, "method": "reciprocal-rank-fusion"}

    @app.post("/api/v1/ranking/rank", tags=["ranking"])
    async def rank_candidates(payload: dict[str, Any]):
        try:
            results = fuse(
                payload.get("lists"),
                payload.get("top_k", 10),
                payload.get("k", DEFAULT_K),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "results": results}

    @app.post("/api/v1/documents", response_model=DocumentOut, status_code=201, tags=["documents"])
    async def ingest_document(request: DocumentIn):
        try:
            return await documents.ingest(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="document ingestion failed") from exc

    @app.post("/api/v1/search", response_model=list[SearchHit], tags=["search"])
    async def search_documents(request: SearchIn):
        try:
            return await search.search(request.query, request.limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="search failed") from exc

    return app


app = create_app()
