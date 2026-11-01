from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .chunking import Chunker
from .embeddings import configured_embedding_provider
from .models import DocumentIn, DocumentOut, SearchIn, SearchHit
from .services import DocumentService, SearchService
from .store import DocumentStore, SqlDocumentStore


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
) -> FastAPI:
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
        title="Ensemble API",
        version="0.1",
        description="REST-first memory, knowledge, and retrieval platform.",
        lifespan=lifespan,
    )
    documents = DocumentService(document_store, Chunker(), embeddings)
    search = SearchService(document_store, embeddings)

    app.state.store = document_store
    app.state.embedding_provider = embeddings

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
        }

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
