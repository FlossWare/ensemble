from __future__ import annotations

import re
import hashlib
import math
import asyncio
import os
from collections.abc import Sequence
from uuid import uuid5, NAMESPACE_URL
from .chunking import Chunker, chunk as chunk_text
from .embeddings import EmbeddingProvider, MAX_TEXTS as MAX_EMBEDDING_BATCH
from .models import Chunk, Document, DocumentIn, DocumentOut, SearchHit
from .store import DocumentStore
from .reranking import Reranker
from .vector_store import VectorStore


class DocumentService:
    def __init__(self, store: DocumentStore, chunker: Chunker, embeddings: EmbeddingProvider, vector_store: VectorStore | None = None):
        self.store = store
        self.chunker = chunker
        self.embeddings = embeddings
        self.vector_store = vector_store

    async def ingest(self, request: DocumentIn, *, embed: bool = True, vector_collection: str | None = None) -> DocumentOut:
        document = Document(request.source_id, request.revision, request.title, request.content, request.metadata)
        document.id = uuid5(NAMESPACE_URL, f"ensemble:document:{request.source_id}:{request.revision}")
        records = chunk_text(request.chunk_profile, request.source_id, request.content, request.media_type)
        if not records:
            raise ValueError("document content contains no usable text")
        texts = [record["text"] for record in records]
        if embed and self.embeddings.available:
            try:
                vectors = []
                for offset in range(0, len(texts), MAX_EMBEDDING_BATCH):
                    vectors.extend(await self.embeddings.embed(
                        texts[offset:offset + MAX_EMBEDDING_BATCH], mode="document"
                    ))
            except Exception:
                if vector_collection is None:
                    raise
                vectors = [[] for _ in texts]
        else:
            vectors = [[] for _ in texts]
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
        requested_document_id = document.id
        document = await self.store.save(document, chunks)
        if document.id != requested_document_id:
            for chunk in chunks:
                chunk.document_id = document.id
                chunk.id = uuid5(NAMESPACE_URL, f"ensemble:{document.id}:{chunk.ordinal}")
            document = await self.store.save(document, chunks)
        vector_indexed = False
        digest = hashlib.sha256(request.content.encode("utf-8")).hexdigest()
        if vector_collection and embed and self.vector_store is not None:
            if self.embeddings.available and chunks and all(chunk.embedding for chunk in chunks):
                status = self.embeddings.status()
                model = status.get("model") or "unknown"
                dimensions = status.get("dimensions") or len(chunks[0].embedding)
                try:
                    items = [{
                        "id": str(chunk.id),
                        "embedding": chunk.embedding,
                        "document": chunk.content,
                        "metadata": {
                            "source_id": document.source_id,
                            "url": document.source_id,
                            "revision": document.revision,
                            "title": document.title,
                            "chunk_id": str(chunk.id),
                            "chunk_ordinal": chunk.ordinal,
                            "chunk_profile": chunk.profile,
                            "chunk_start": chunk.start,
                            "chunk_end": chunk.end,
                            "chunk_header": chunk.header,
                            "content_sha256": digest,
                            "source_metadata": document.metadata,
                        },
                    } for chunk in chunks]
                    for offset in range(0, len(items), 256):
                        await self.vector_store.upsert(
                            vector_collection, model, dimensions, items[offset:offset + 256]
                        )
                    vector_indexed = True
                except Exception:
                    vector_indexed = False
            # The durable document revision is authoritative. Never leave vectors
            # from an older revision searchable after a newer revision is accepted.
            try:
                await self.vector_store.delete_superseded(
                    vector_collection, document.source_id, digest
                )
            except Exception:
                vector_indexed = False
        return DocumentOut(
            id=document.id,
            source_id=document.source_id,
            revision=document.revision,
            title=document.title,
            chunk_count=len(chunks),
            embedded=all(bool(chunk.embedding) for chunk in chunks),
            created_at=document.created_at,
            chunk_profile=request.chunk_profile,
            vector_indexed=vector_indexed,
            metadata=document.metadata,
        )


class SearchService:
    def __init__(self, store: DocumentStore, embeddings: EmbeddingProvider, reranker: Reranker | None = None, vector_store: VectorStore | None = None):
        self.store = store
        self.embeddings = embeddings
        self.reranker = reranker
        self.vector_store = vector_store

    async def lexical_search(self, query: str, limit: int = 10) -> list[SearchHit]:
        candidates = await self.search_with_status(
            query, min(2000, limit * 4), include_vector=False, rerank=False
        )
        seen: set[str] = set()
        unique: list[SearchHit] = []
        for hit in candidates["results"]:
            digest = hashlib.sha256(hit.text.encode("utf-8")).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            unique.append(hit)
            if len(unique) >= limit:
                break
        return unique

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

        lexical_matches = await self.store.lexical_search(query, limit=min(2000, max(200, limit * 4)))
        lexical_scores = {
            item.chunk.id: score for item, score in lexical_matches
        }
        vector_scores: dict = {}
        degraded: list[str] = []
        if include_vector and self.embeddings.available:
            try:
                query_vector = (await self.embeddings.embed([query], mode="query"))[0]
                if self.vector_store is not None:
                    vector_hits = await self.vector_store.query("transcript_text", query_vector, 200)
                    known_ids = {str(item.chunk.id): item.chunk.id for item in indexed}
                    vector_scores = {
                        known_ids[hit["id"]]: float(hit["score"])
                        for hit in vector_hits if hit["id"] in known_ids and float(hit["score"]) > 0
                    }
                # Keep document chunks outside the transcript collection in the
                # vector candidate set as well; the collection is one source, not
                # a replacement for the document-store vector channel.
                for item in indexed:
                    if item.chunk.embedding:
                        score = _cosine(query_vector, item.chunk.embedding)
                        if score > 0:
                            vector_scores[item.chunk.id] = max(
                                vector_scores.get(item.chunk.id, 0.0), score
                            )
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
                session_id=item.document.metadata.get("session_id"),
                project=item.document.metadata.get("project"),
                role=item.document.metadata.get("role"),
                kind=item.document.metadata.get("kind"),
                is_subagent=item.document.metadata.get("is_subagent"),
                text_sha=hashlib.sha256(item.chunk.content.encode("utf-8")).hexdigest(),
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
