from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid5, NAMESPACE_URL

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .chunking import Chunker
from .embeddings import EmbeddingProvider
from .models import Chunk, Document, DocumentOut
from .store import DocumentStore, IndexedChunk


class ChunkRequest(BaseModel):
    text: str = Field(min_length=1)
    target_characters: int = Field(default=1200, ge=1)
    overlap_characters: int = Field(default=160, ge=0)


class EmbeddingRequest(BaseModel):
    texts: list[str]


class IndexSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=200, ge=1, le=1000)


class IndexDocumentRequest(BaseModel):
    source_id: str
    revision: int
    title: str
    content: str
    chunks: list[str]
    embeddings: list[list[float]]


def create_capability_router(store: DocumentStore, embeddings: EmbeddingProvider) -> APIRouter:
    router = APIRouter()
    chunker = Chunker()

    @router.post("/capabilities/chunking/split", tags=["capabilities", "chunking"])
    async def split_chunks(request: ChunkRequest):
        try:
            return {"chunks": chunker.split(
                request.text, request.target_characters, request.overlap_characters
            )}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/capabilities/embeddings", tags=["capabilities", "embeddings"])
    async def create_embeddings(request: EmbeddingRequest):
        vectors = await embeddings.embed(request.texts) if embeddings.available else [
            [] for _ in request.texts
        ]
        if len(vectors) != len(request.texts):
            raise HTTPException(status_code=502, detail="embedding provider returned an unexpected vector count")
        return {"available": embeddings.available, "embeddings": vectors}

    @router.post(
        "/capabilities/index/documents", response_model=DocumentOut,
        status_code=201, tags=["capabilities", "index"],
    )
    async def index_document(request: IndexDocumentRequest):
        if len(request.chunks) != len(request.embeddings):
            raise HTTPException(status_code=400, detail="chunk and embedding counts differ")
        document = Document(request.source_id, request.revision, request.title, request.content)
        chunks = [
            Chunk(document.id, ordinal, content, request.embeddings[ordinal])
            for ordinal, content in enumerate(request.chunks)
        ]
        for chunk in chunks:
            chunk.id = uuid5(NAMESPACE_URL, f"ensemble:{document.id}:{chunk.ordinal}")
        try:
            document = await store.save(document, chunks)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="index persistence failed") from exc
        return DocumentOut(
            id=document.id, source_id=document.source_id, revision=document.revision,
            title=document.title, chunk_count=len(chunks),
            embedded=all(bool(chunk.embedding) for chunk in chunks),
            created_at=document.created_at,
        )

    @router.post("/capabilities/index/search-candidates", tags=["capabilities", "index"])
    async def read_indexed_chunks(request: IndexSearchRequest):
        indexed = await store.all_chunks(request.query, request.limit)
        return [
            {
                "document": {
                    "id": str(item.document.id), "source_id": item.document.source_id,
                    "revision": item.document.revision, "title": item.document.title,
                    "content": item.document.content,
                    "created_at": item.document.created_at.isoformat(),
                },
                "chunk": {
                    "id": str(item.chunk.id), "ordinal": item.chunk.ordinal,
                    "content": item.chunk.content, "embedding": item.chunk.embedding,
                },
            }
            for item in indexed
        ]

    return router


class CapabilityClient:
    """HTTP client for Ensemble capability contracts.

    ASGI transport keeps the initial deployment co-located while preserving HTTP
    routing, request validation, and JSON serialization at every capability edge.
    """

    def __init__(self, app):
        self.app = app

    async def _request(self, method: str, path: str, **kwargs):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ensemble") as client:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

    async def split(self, text: str) -> list[str]:
        result = await self._request(
            "POST", "/api/v1/capabilities/chunking/split", json={"text": text}
        )
        return result["chunks"]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        result = await self._request(
            "POST", "/api/v1/capabilities/embeddings", json={"texts": texts}
        )
        return result["embeddings"]

    async def index_document(
        self, source_id: str, revision: int, title: str, content: str,
        chunks: list[str], vectors: list[list[float]],
    ) -> dict:
        return await self._request(
            "POST", "/api/v1/capabilities/index/documents",
            json={
                "source_id": source_id, "revision": revision, "title": title,
                "content": content, "chunks": chunks, "embeddings": vectors,
            },
        )

    async def vector_candidates(self, embedding: list[float], limit: int) -> list[IndexedChunk]:
        rows = await self._request(
            "POST", "/api/v1/capabilities/index/vector-search",
            json={"embedding": embedding, "limit": limit},
        )
        return self._decode_indexed(rows)

    async def indexed_chunks(self, query: str, limit: int) -> list[IndexedChunk]:
        rows = await self._request(
            "POST", "/api/v1/capabilities/index/search-candidates",
            json={"query": query, "limit": limit},
        )
        return self._decode_indexed(rows)

    @staticmethod
    def _decode_indexed(rows: list[dict]) -> list[IndexedChunk]:
        result: list[IndexedChunk] = []
        for row in rows:
            raw_document, raw_chunk = row["document"], row["chunk"]
            document = Document(
                raw_document["source_id"], raw_document["revision"],
                raw_document["title"], raw_document["content"],
            )
            document.id = UUID(raw_document["id"])
            document.created_at = datetime.fromisoformat(raw_document["created_at"])
            chunk = Chunk(
                document.id, raw_chunk["ordinal"], raw_chunk["content"],
                raw_chunk["embedding"],
            )
            chunk.id = UUID(raw_chunk["id"])
            result.append(IndexedChunk(document, chunk))
        return result
