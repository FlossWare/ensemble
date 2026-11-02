from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .auth import authenticate, load_api_keys
from .capabilities import (
    CapabilityClient, ChunkRequest, EmbeddingRequest, IndexDocumentRequest,
    create_capability_router,
)
from .embeddings import configured_embedding_provider
from .reranking import configured_reranker
from .knowledge_store import KnowledgeStore
from .models import DocumentIn, DocumentOut, SearchIn, SearchHit, Document, Chunk
from .services import search_indexed
from .store import DocumentStore, IndexedChunk, SqlDocumentStore


def _problem(request: Request, status: int, title: str, detail: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": "about:blank", "title": title, "status": status,
            "detail": detail, "instance": request.url.path,
            "code": code, "correlationId": str(uuid4()),
        },
    )


def create_app(database_url: str | None = None, store: DocumentStore | None = None) -> FastAPI:
    document_store = store or SqlDocumentStore(database_url)
    api_keys = load_api_keys()
    embeddings = configured_embedding_provider()
    reranker = configured_reranker()
    knowledge_store = KnowledgeStore(document_store.engine) if hasattr(document_store, "engine") else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await document_store.initialize()
            yield
        finally:
            await document_store.close()

    app = FastAPI(
        title="Ensemble API", version="0.1",
        description="REST-first memory, knowledge, and retrieval platform.",
        lifespan=lifespan,
    )
    app.state.store = document_store
    app.state.embedding_provider = embeddings
    app.state.knowledge_store = knowledge_store
    app.include_router(create_capability_router(document_store, embeddings, reranker, knowledge_store), prefix="/api/v1")



    @app.exception_handler(HTTPException)
    async def http_problem(request: Request, exc: HTTPException):
        title = "Client Error" if exc.status_code < 500 else "Server Error"
        detail = exc.detail if isinstance(exc.detail, str) else title
        return _problem(request, exc.status_code, title, detail, "http-error")

    @app.exception_handler(RequestValidationError)
    async def validation_problem(request: Request, exc: RequestValidationError):
        return _problem(request, 422, "Unprocessable Content", "Request validation failed", "request-validation-failed")

    @app.middleware("http")
    async def authenticate_api_requests(request: Request, call_next):
        if not request.url.path.startswith("/api/v1/"):
            return await call_next(request)
        if not api_keys:
            return _problem(
                request, 503, "Service Unavailable",
                "API authentication is not configured", "authentication-not-configured",
            )
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        principal = authenticate(token, api_keys) if scheme.lower() == "bearer" and token else None
        if principal is None:
            return _problem(request, 401, "Unauthorized", "A valid bearer API key is required", "invalid-api-key")
        path = request.url.path
        read_only = (
            request.method in {"GET", "HEAD"}
            or path.endswith("/search")
            or path.endswith("/search-candidates")
            or path.endswith("/vector-search")
            or path.endswith("/embeddings")
            or path.endswith("/rerank")
            or path.endswith("/tokenize")
        )
        required = "read" if read_only else "write"
        if required not in principal.permissions and "admin" not in principal.permissions:
            return _problem(request, 403, "Forbidden", f"{required} permission is required", "insufficient-permission")
        partition_id = request.headers.get("x-ensemble-partition")
        if not partition_id:
            if len(principal.partitions) == 1 and "*" not in principal.partitions:
                partition_id = next(iter(principal.partitions))
            else:
                return _problem(request, 400, "Bad Request", "X-Ensemble-Partition is required", "partition-required")
        if "*" not in principal.partitions and partition_id not in principal.partitions:
            return _problem(request, 403, "Forbidden", "The API key is not authorized for this partition", "partition-forbidden")
        request.state.principal = principal
        request.state.partition_id = partition_id
        return await call_next(request)

    @app.get("/health", tags=["operations"])
    async def health():
        return {"status": "ok", "service": "ensemble"}

    @app.get("/ready", tags=["operations"])
    async def ready():
        try:
            await document_store.check_ready()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="persistence unavailable") from exc
        return {"status": "ready", "persistence": "durable", "embeddings": embeddings.available, "reranking": reranker.available}

    @app.post("/api/v1/documents", response_model=DocumentOut, status_code=201, tags=["documents"])
    async def ingest_document(document_request: DocumentIn, request: Request):
        client = CapabilityClient(
            app, request.headers["authorization"], request.state.partition_id
        )
        try:
            chunks = await client.split(document_request.content)
            if not chunks:
                raise HTTPException(status_code=400, detail="document content contains no usable text")
            vectors = await client.embed(chunks)
            return await client.index_document(
                document_request.source_id, document_request.revision,
                document_request.title, document_request.content, chunks, vectors
            )
        except HTTPException:
            raise
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=502, detail="document capability request failed") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="document ingestion failed") from exc

    @app.post("/api/v1/search", response_model=list[SearchHit], tags=["search"])
    async def search_documents(search_request: SearchIn, request: Request):
        client = CapabilityClient(
            app, request.headers["authorization"], request.state.partition_id
        )
        try:
            candidate_limit = min(max(search_request.limit * 20, 100), 1000)
            lexical = await client.indexed_chunks(search_request.query, candidate_limit)
            query_vectors = await client.embed([search_request.query])
            query_vector = query_vectors[0] if query_vectors else []
            vector = (
                await client.vector_candidates(query_vector, candidate_limit)
                if query_vector else []
            )
            candidates = {item.chunk.id: item for item in lexical}
            candidates.update({item.chunk.id: item for item in vector})
            return search_indexed(
                list(candidates.values()), search_request.query, search_request.limit, query_vector
            )
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=502, detail="search capability request failed") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="search failed") from exc

    return app


app = create_app()
