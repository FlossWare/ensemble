from __future__ import annotations

import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from .chunking import Chunker
from .embeddings import configured_embedding_provider
from .models import DocumentIn, DocumentOut, SearchIn, SearchHit
from .services import DocumentService, SearchService
from .store import InMemoryDocumentStore


def create_app() -> FastAPI:
    app = FastAPI(
        title="Ensemble API",
        version="0.1",
        description="REST-first memory, knowledge, and retrieval platform.",
    )
    store = InMemoryDocumentStore()
    embeddings = configured_embedding_provider()
    documents = DocumentService(store, Chunker(), embeddings)
    search = SearchService(store, embeddings)

    app.state.store = store
    app.state.embedding_provider = embeddings

    @app.get("/health", tags=["operations"])
    async def health():
        return {"status": "ok", "service": "ensemble"}

    @app.get("/ready", tags=["operations"])
    async def ready():
        return {"status": "ready", "persistence": "memory", "embeddings": embeddings.available}

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
