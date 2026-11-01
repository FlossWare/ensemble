from __future__ import annotations

import re
import math
from collections.abc import Sequence
from uuid import uuid5, NAMESPACE_URL
from .chunking import Chunker
from .embeddings import EmbeddingProvider
from .models import Chunk, Document, DocumentIn, DocumentOut, SearchHit
from .store import DocumentStore


class DocumentService:
    def __init__(self, store: DocumentStore, chunker: Chunker, embeddings: EmbeddingProvider):
        self.store = store
        self.chunker = chunker
        self.embeddings = embeddings

    async def ingest(self, request: DocumentIn) -> DocumentOut:
        document = Document(request.source_id, request.revision, request.title, request.content)
        texts = self.chunker.split(request.content)
        if not texts:
            raise ValueError("document content contains no usable text")
        vectors = await self.embeddings.embed(texts, mode="document") if self.embeddings.available else [[] for _ in texts]
        if len(vectors) != len(texts):
            raise RuntimeError("embedding provider returned an unexpected vector count")
        chunks = [
            Chunk(document.id, ordinal, text, vectors[ordinal])
            for ordinal, text in enumerate(texts)
        ]
        for chunk in chunks:
            chunk.id = uuid5(NAMESPACE_URL, f"ensemble:{document.id}:{chunk.ordinal}")
        document = await self.store.save(document, chunks)
        return DocumentOut(
            id=document.id,
            source_id=document.source_id,
            revision=document.revision,
            title=document.title,
            chunk_count=len(chunks),
            embedded=all(bool(chunk.embedding) for chunk in chunks),
            created_at=document.created_at,
        )


class SearchService:
    def __init__(self, store: DocumentStore, embeddings: EmbeddingProvider):
        self.store = store
        self.embeddings = embeddings

    async def search(self, query: str, limit: int = 10) -> list[SearchHit]:
        if not query.strip():
            raise ValueError("query is required")
        indexed = await self.store.all_chunks()
        if not indexed:
            return []

        query_terms = _tokens(query)
        lexical_scores = {
            item.chunk.id: len(query_terms & _tokens(item.chunk.content)) / len(query_terms)
            for item in indexed
            if query_terms and query_terms & _tokens(item.chunk.content)
        }
        vector_scores: dict = {}
        if self.embeddings.available:
            query_vector = (await self.embeddings.embed([query], mode="query"))[0]
            vector_scores = {
                item.chunk.id: score
                for item in indexed
                if item.chunk.embedding
                and (score := _cosine(query_vector, item.chunk.embedding)) > 0
            }

        lexical_ranks = _rank(lexical_scores)
        vector_ranks = _rank(vector_scores)
        hits: list[SearchHit] = []
        for item in indexed:
            lexical_rank = lexical_ranks.get(item.chunk.id)
            vector_rank = vector_ranks.get(item.chunk.id)
            if lexical_rank is None and vector_rank is None:
                continue
            fusion = _rrf(lexical_rank) + _rrf(vector_rank)
            hits.append(SearchHit(
                document_id=item.document.id,
                source_id=item.document.source_id,
                revision=item.document.revision,
                title=item.document.title,
                chunk_id=item.chunk.id,
                chunk_ordinal=item.chunk.ordinal,
                text=item.chunk.content,
                lexical_score=lexical_scores.get(item.chunk.id, 0.0),
                vector_score=vector_scores.get(item.chunk.id, 0.0),
                fusion_score=fusion,
            ))
        return sorted(hits, key=lambda hit: (hit.fusion_score, hit.lexical_score), reverse=True)[:limit]


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[^\W_]+", text.casefold()) if token}


def _rank(scores: dict) -> dict:
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return {identifier: rank for rank, (identifier, _) in enumerate(ordered, start=1)}


def _rrf(rank: int | None, constant: int = 60) -> float:
    return 0.0 if rank is None else 1.0 / (constant + rank)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return 0.0 if not left_norm or not right_norm else dot / (left_norm * right_norm)
