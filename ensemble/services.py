from __future__ import annotations

import re
import math
from collections.abc import Sequence

from .embeddings import EmbeddingProvider
from .models import SearchHit
from .store import DocumentStore, IndexedChunk


class SearchService:
    """Adapter-level search service; HTTP orchestration uses search_indexed."""

    def __init__(self, store: DocumentStore, embeddings: EmbeddingProvider):
        self.store = store
        self.embeddings = embeddings

    async def search(self, query: str, limit: int = 10) -> list[SearchHit]:
        if not query.strip():
            raise ValueError("query is required")
        indexed = await self.store.all_chunks()
        query_vector = (
            (await self.embeddings.embed([query]))[0]
            if self.embeddings.available else []
        )
        return search_indexed(indexed, query, limit, query_vector)


def search_indexed(
    indexed: list[IndexedChunk],
    query: str,
    limit: int = 10,
    query_vector: list[float] | None = None,
) -> list[SearchHit]:
    if not query.strip():
        raise ValueError("query is required")
    if not indexed:
        return []

    query_terms = _tokens(query)
    lexical_scores = {
        item.chunk.id: len(query_terms & _tokens(item.chunk.content)) / len(query_terms)
        for item in indexed
        if query_terms and query_terms & _tokens(item.chunk.content)
    }
    vector_scores: dict = {}
    if query_vector:
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
            fusion_score=_rrf(lexical_rank) + _rrf(vector_rank),
        ))
    return sorted(
        hits, key=lambda hit: (hit.fusion_score, hit.lexical_score), reverse=True
    )[:limit]


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
