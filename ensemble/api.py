from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .capabilities import (
    CapabilityClient, ChunkRequest, EmbeddingRequest, IndexDocumentRequest,
    create_capability_router,
)
from .embeddings import configured_embedding_provider
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
    embeddings = configured_embedding_provider()

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
    app.include_router(create_capability_router(document_store, embeddings), prefix="/api/v1")

    client = CapabilityClient(app)

    @app.exception_handler(HTTPException)
    async def http_problem(request: Request, exc: HTTPException):
        title = "Client Error" if exc.status_code < 500 else "Server Error"
        detail = exc.detail if isinstance(exc.detail, str) else title
        return _problem(request, exc.status_code, title, detail, "http-error")

    @app.exception_handler(RequestValidationError)
    async def validation_problem(request: Request, exc: RequestValidationError):
        return _problem(request, 422, "Unprocessable Content", "Request validation failed", "request-validation-failed")

    @app.get("/health", tags=["operations"])
    async def health():
        return {"status": "ok", "service": "ensemble"}

    @app.get("/ready", tags=["operations"])
    async def ready():
        try:
            await document_store.check_ready()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="persistence unavailable") from exc
        return {"status": "ready", "persistence": "durable", "embeddings": embeddings.available}

    @app.post("/api/v1/documents", response_model=DocumentOut, status_code=201, tags=["documents"])
    async def ingest_document(request: DocumentIn):
        try:
            chunks = await client.split(request.content)
            if not chunks:
                raise HTTPException(status_code=400, detail="document content contains no usable text")
            vectors = await client.embed(chunks)
            return await client.index_document(
                request.source_id, request.revision, request.title, request.content, chunks, vectors
            )
        except HTTPException:
            raise
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=502, detail="document capability request failed") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="document ingestion failed") from exc

    @app.post("/api/v1/search", response_model=list[SearchHit], tags=["search"])
    async def search_documents(request: SearchIn):
        try:
            candidate_limit = min(max(request.limit * 20, 100), 1000)
            lexical = await client.indexed_chunks(request.query, candidate_limit)
            query_vectors = await client.embed([request.query])
            query_vector = query_vectors[0] if query_vectors else []
            vector = (
                await client.vector_candidates(query_vector, candidate_limit)
                if query_vector else []
            )
            candidates = {item.chunk.id: item for item in lexical}
            candidates.update({item.chunk.id: item for item in vector})
            return search_indexed(
                list(candidates.values()), request.query, request.limit, query_vector
            )
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=502, detail="search capability request failed") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="search failed") from exc

    return app


app = create_app()
