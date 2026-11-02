from __future__ import annotations

import re
import math
import asyncio
import os
from collections.abc import Sequence
from uuid import uuid5, NAMESPACE_URL
from .chunking import Chunker, chunk as chunk_text
from .embeddings import EmbeddingProvider
from .models import Chunk, Document, DocumentIn, DocumentOut, SearchHit
from .store import DocumentStore
from .reranking import Reranker


class DocumentService:
    def __init__(self, store: DocumentStore, chunker: Chunker, embeddings: EmbeddingProvider):
        self.store = store
        self.chunker = chunker
        self.embeddings = embeddings

    async def ingest(self, request: DocumentIn, *, embed: bool = True) -> DocumentOut:
        document = Document(request.source_id, request.revision, request.title, request.content)
        records = chunk_text(request.chunk_profile, request.source_id, request.content, request.media_type)
        if not records:
            raise ValueError("document content contains no usable text")
        texts = [record["text"] for record in records]
        vectors = await self.embeddings.embed(texts, mode="document") if embed and self.embeddings.available else [[] for _ in texts]
        if len(vectors) != len(texts):
            raise RuntimeError("embedding provider returned an unexpected vector count")
        chunks = [
            Chunk(
                document.id, record["index"], record["text"], vectors[index],
                profile=request.chunk_profile, start=record["start"], end=record["end"],
                header=record["header"],
            )
            for index, record in enumerate(records)
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
            chunk_profile=request.chunk_profile,
        )


class SearchService:
    def __init__(self, store: DocumentStore, embeddings: EmbeddingProvider, reranker: Reranker | None = None):
        self.store = store
        self.embeddings = embeddings
        self.reranker = reranker

    async def lexical_search(self, query: str, limit: int = 10) -> list[SearchHit]:
        return (await self.search_with_status(query, limit, include_vector=False))["results"]

    async def search(self, query: str, limit: int = 10) -> list[SearchHit]:
        return (await self.search_with_status(query, limit))["results"]

    async def search_with_status(
        self, query: str, limit: int = 10, *, include_vector: bool = True, rerank: bool | None = None
    ) -> dict:
        if not query.strip():
            raise ValueError("query is required")
        indexed = await self.store.all_chunks()
        if not indexed:
            return {
                "ok": True, "results": [], "degraded": [],
                "retrieval": "lexical", "reranked": False,
            }

        lexical_matches = await self.store.lexical_search(query, limit=200)
        lexical_scores = {
            item.chunk.id: score for item, score in lexical_matches
        }
        vector_scores: dict = {}
        degraded: list[str] = []
        if include_vector and self.embeddings.available:
            try:
                query_vector = (await self.embeddings.embed([query], mode="query"))[0]
                vector_scores = {
                    item.chunk.id: score
                    for item in indexed
                    if item.chunk.embedding
                    and (score := _cosine(query_vector, item.chunk.embedding)) > 0
                }
            except Exception:
                # Preserve lexical results when the embedding dependency is degraded.
                degraded.append("embeddings")

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
                chunk_profile=item.chunk.profile,
                chunk_start=item.chunk.start,
                chunk_end=item.chunk.end,
                chunk_header=item.chunk.header,
            ))
        results = sorted(
            hits, key=lambda hit: (hit.fusion_score, hit.lexical_score, str(hit.chunk_id)),
            reverse=True,
        )[:limit]
        reranked = False
        rerank_enabled = (
            os.getenv("ENSEMBLE_RERANK_ENABLED", "0").strip() == "1"
            if rerank is None else rerank
        )
        if rerank_enabled and results:
            if self.reranker is None or not self.reranker.available:
                degraded.append("reranking")
            else:
                candidates = results[:min(30, len(results))]
                try:
                    scores = await asyncio.to_thread(
                        self.reranker.score, query,
                        [candidate.text[:4000] for candidate in candidates],
                    )
                    if len(scores) != len(candidates) or any(not math.isfinite(float(score)) for score in scores):
                        raise RuntimeError("reranker returned invalid or misaligned scores")
                    scored = [
                        candidate.model_copy(update={"rerank_score": float(score)})
                        for candidate, score in zip(candidates, scores)
                    ]
                    results = [
                        item for _, item in sorted(
                            enumerate(scored), key=lambda pair: (-pair[1].rerank_score, pair[0])
                        )
                    ] + results[len(candidates):]
                    reranked = True
                except Exception:
                    degraded.append("reranking")
        return {
            "ok": True,
            "results": results,
            "degraded": degraded,
            "retrieval": "hybrid" if vector_scores else "lexical",
            "reranked": reranked,
        }


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
