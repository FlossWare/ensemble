from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid5, NAMESPACE_URL

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .chunking import Chunker
from .embeddings import EmbeddingProvider
from .models import Chunk, Document, DocumentOut
from .store import DocumentStore, IndexedChunk
from .reranking import Reranker
from .tokenization import TOKENIZER_ID, tokenize, split_by_tokens
from .knowledge_store import KnowledgeStore
from .extraction import extract_text


class ChunkRequest(BaseModel):
    text: str = Field(min_length=1)
    target_characters: int = Field(default=1200, ge=1)
    overlap_characters: int = Field(default=160, ge=0)


class EmbeddingRequest(BaseModel):
    texts: list[str]


class ExtractionRequest(BaseModel):
    media_type: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1)


class MemoryUpsertRequest(BaseModel):
    memory_key: str = Field(min_length=1, max_length=512)
    category: str = Field(default="general", min_length=1, max_length=128)
    content: str = Field(min_length=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    metadata: dict = Field(default_factory=dict)
    expires_at: datetime | None = None


class MemorySearchRequest(BaseModel):
    query: str = ""
    limit: int = Field(default=10, ge=1, le=100)


class GraphNodeRequest(BaseModel):
    node_type: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=512)
    properties: dict = Field(default_factory=dict)


class GraphEdgeRequest(BaseModel):
    source_node_id: str = Field(min_length=36, max_length=36)
    target_node_id: str = Field(min_length=36, max_length=36)
    relation: str = Field(min_length=1, max_length=128)
    properties: dict = Field(default_factory=dict)


class TokenChunkRequest(BaseModel):
    text: str = Field(min_length=1)
    target_tokens: int = Field(default=256, ge=1, le=8192)
    overlap_tokens: int = Field(default=32, ge=0)


class TokenizationRequest(BaseModel):
    text: str


class RerankRequest(BaseModel):
    query: str = Field(min_length=1)
    documents: list[str] = Field(min_length=1, max_length=100)
    top_n: int = Field(default=10, ge=1, le=100)


class IndexSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=200, ge=1, le=1000)


class VectorSearchRequest(BaseModel):
    embedding: list[float] = Field(min_length=1)
    limit: int = Field(default=200, ge=1, le=1000)


class IndexDocumentRequest(BaseModel):
    source_id: str
    revision: int
    title: str
    content: str
    chunks: list[str]
    embeddings: list[list[float]]


def create_capability_router(store: DocumentStore, embeddings: EmbeddingProvider, reranker: Reranker, knowledge_store: KnowledgeStore | None) -> APIRouter:
    router = APIRouter()
    chunker = Chunker()

    @router.post("/capabilities/extraction/text", tags=["capabilities", "extraction"])
    async def extract_content(request: ExtractionRequest):
        try:
            text = extract_text(request.content, request.media_type)
        except ValueError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        return {"text": text, "character_count": len(text)}

    @router.post("/capabilities/memory", tags=["capabilities", "memory"])
    async def upsert_memory(request: MemoryUpsertRequest, http_request: Request):
        if knowledge_store is None:
            raise HTTPException(status_code=503, detail="memory persistence is unavailable")
        return await knowledge_store.upsert_memory(
            http_request.state.partition_id, request.memory_key, request.category,
            request.content, request.importance, request.metadata, request.expires_at,
        )

    @router.get("/capabilities/memory/{memory_key}", tags=["capabilities", "memory"])
    async def get_memory(memory_key: str, http_request: Request):
        if knowledge_store is None:
            raise HTTPException(status_code=503, detail="memory persistence is unavailable")
        result = await knowledge_store.get_memory(http_request.state.partition_id, memory_key)
        if result is None:
            raise HTTPException(status_code=404, detail="memory not found")
        return result

    @router.post("/capabilities/memory/search", tags=["capabilities", "memory"])
    async def search_memories(request: MemorySearchRequest, http_request: Request):
        if knowledge_store is None:
            raise HTTPException(status_code=503, detail="memory persistence is unavailable")
        return await knowledge_store.search_memories(
            http_request.state.partition_id, request.query, request.limit
        )

    @router.post("/capabilities/graph/nodes", tags=["capabilities", "graph"])
    async def upsert_graph_node(request: GraphNodeRequest, http_request: Request):
        if knowledge_store is None:
            raise HTTPException(status_code=503, detail="graph persistence is unavailable")
        return await knowledge_store.upsert_node(
            http_request.state.partition_id, request.node_type, request.label, request.properties
        )

    @router.post("/capabilities/graph/edges", tags=["capabilities", "graph"])
    async def upsert_graph_edge(request: GraphEdgeRequest, http_request: Request):
        if knowledge_store is None:
            raise HTTPException(status_code=503, detail="graph persistence is unavailable")
        try:
            return await knowledge_store.upsert_edge(
                http_request.state.partition_id, request.source_node_id,
                request.target_node_id, request.relation, request.properties,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/capabilities/graph/nodes/{node_id}/neighbors", tags=["capabilities", "graph"])
    async def traverse_graph(node_id: str, http_request: Request, depth: int = 1):
        if knowledge_store is None:
            raise HTTPException(status_code=503, detail="graph persistence is unavailable")
        if depth < 1 or depth > 4:
            raise HTTPException(status_code=400, detail="depth must be between 1 and 4")
        try:
            return await knowledge_store.traverse(http_request.state.partition_id, node_id, depth)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/capabilities/chunking/split-tokens", tags=["capabilities", "chunking", "tokenization"])
    async def split_token_chunks(request: TokenChunkRequest):
        if request.overlap_tokens >= request.target_tokens:
            raise HTTPException(status_code=400, detail="overlap_tokens must be less than target_tokens")
        return {
            "tokenizer": TOKENIZER_ID,
            "chunks": split_by_tokens(request.text, request.target_tokens, request.overlap_tokens),
        }

    @router.post("/capabilities/tokenization/tokenize", tags=["capabilities", "tokenization"])
    async def tokenize_text(request: TokenizationRequest):
        tokens = tokenize(request.text)
        return {"tokenizer": TOKENIZER_ID, "tokens": tokens, "token_count": len(tokens)}

    @router.post("/capabilities/reranking/rerank", tags=["capabilities", "reranking"])
    async def rerank_documents(request: RerankRequest):
        if not reranker.available:
            raise HTTPException(status_code=503, detail="reranking provider is not configured")
        try:
            return {"results": await reranker.rerank(request.query, request.documents, request.top_n)}
        except Exception as exc:
            raise HTTPException(status_code=502, detail="reranking provider request failed") from exc

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
    async def index_document(request: IndexDocumentRequest, http_request: Request):
        if len(request.chunks) != len(request.embeddings):
            raise HTTPException(status_code=400, detail="chunk and embedding counts differ")
        document = Document(request.source_id, request.revision, request.title, request.content, http_request.state.partition_id)
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
            partition_id=document.partition_id, title=document.title, chunk_count=len(chunks),
            embedded=all(bool(chunk.embedding) for chunk in chunks),
            created_at=document.created_at,
        )

    @router.post("/capabilities/index/vector-search", tags=["capabilities", "index", "vector"])
    async def vector_search(request: VectorSearchRequest, http_request: Request):
        indexed = await store.vector_candidates(request.embedding, request.limit, http_request.state.partition_id)
        return [
            {
                "document": {
                    "id": str(item.document.id), "source_id": item.document.source_id, "partition_id": item.document.partition_id,
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

    @router.post("/capabilities/index/search-candidates", tags=["capabilities", "index"])
    async def read_indexed_chunks(request: IndexSearchRequest, http_request: Request):
        indexed = await store.all_chunks(request.query, request.limit, http_request.state.partition_id)
        return [
            {
                "document": {
                    "id": str(item.document.id), "source_id": item.document.source_id,
                    "partition_id": item.document.partition_id, "revision": item.document.revision, "title": item.document.title,
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

    def __init__(self, app, authorization: str, partition_id: str):
        self.app = app
        self.headers = {
            "Authorization": authorization,
            "X-Ensemble-Partition": partition_id,
        }

    async def _request(self, method: str, path: str, **kwargs):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ensemble") as client:
            request_headers = {**self.headers, **kwargs.pop("headers", {})}
            response = await client.request(method, path, headers=request_headers, **kwargs)
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
                raw_document["partition_id"],
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
