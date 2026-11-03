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
from .reranking import LocalRankingProvider, Reranker, RerankingDecorator
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
        provider = LocalRankingProvider(reranker) if reranker is not None else None
        self.search_with_status = RerankingDecorator(provider)(self._search_with_status)

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

    async def _search_with_status(
        self, query: str, limit: int = 10, *, include_vector: bool = True, rerank: bool | None = None
    ) -> dict:
        if not query.strip():
            raise ValueError("query is required")

        indexed = await self.store.all_chunks()
        lexical_scores: dict = {}
        vector_scores: dict = {}
        degraded: list[str] = []
        channels = {
            "lexical": {"status": "not_run", "available": False, "candidate_count": 0},
            "vector": {"status": "disabled", "available": False, "candidate_count": 0,
                       "collection_candidate_count": 0, "document_candidate_count": 0},
        }

        # Keep channels independent: one failing must not suppress the other.
        try:
            lexical_matches = await self.store.lexical_search(
                query, limit=min(2000, max(200, limit * 4))
            )
            lexical_scores = {item.chunk.id: score for item, score in lexical_matches}
            channels["lexical"] = {
                "status": "ok", "available": True,
                "candidate_count": len(lexical_scores),
            }
        except Exception:
            channels["lexical"] = {
                "status": "unavailable", "available": False,
                "candidate_count": 0, "error": "lexical search failed",
            }
            degraded.append("lexical")

        if include_vector:
            if not self.embeddings.available:
                channels["vector"] = {
                    "status": "unavailable", "available": False,
                    "candidate_count": 0, "collection_candidate_count": 0,
                    "document_candidate_count": 0, "error": "embedding provider unavailable",
                }
                degraded.append("embeddings")
            else:
                collection_error = False
                try:
                    embedded = await self.embeddings.embed([query], mode="query")
                    if not isinstance(embedded, list) or len(embedded) != 1:
                        raise RuntimeError("embedding provider returned an invalid query vector")
                    query_vector = embedded[0]
                    if not isinstance(query_vector, (list, tuple)) or not query_vector:
                        raise RuntimeError("embedding provider returned an empty query vector")
                    if any(not math.isfinite(float(value)) for value in query_vector):
                        raise RuntimeError("embedding provider returned a non-finite query vector")

                    collection_scores: dict = {}
                    if self.vector_store is not None:
                        try:
                            vector_hits = await self.vector_store.query("transcript_text", query_vector, 200)
                            known_ids = {str(item.chunk.id): item.chunk.id for item in indexed}
                            collection_scores = {
                                known_ids[hit["id"]]: float(hit["score"])
                                for hit in vector_hits
                                if hit.get("id") in known_ids
                                and math.isfinite(float(hit.get("score", 0.0)))
                                and float(hit.get("score", 0.0)) > 0
                            }
                        except Exception:
                            collection_error = True

                    document_scores: dict = {}
                    for item in indexed:
                        if item.chunk.embedding:
                            score = _cosine(query_vector, item.chunk.embedding)
                            if score > 0:
                                document_scores[item.chunk.id] = score
                    vector_scores = dict(collection_scores)
                    for identifier, score in document_scores.items():
                        vector_scores[identifier] = max(vector_scores.get(identifier, 0.0), score)

                    vector_status = "partial" if collection_error else "ok"
                    channels["vector"] = {
                        "status": vector_status, "available": True,
                        "candidate_count": len(vector_scores),
                        "collection_candidate_count": len(collection_scores),
                        "document_candidate_count": len(document_scores),
                    }
                    if collection_error:
                        channels["vector"]["error"] = "vector collection query failed; document vectors remain available"
                        degraded.append("vector_collection")
                except Exception:
                    channels["vector"] = {
                        "status": "unavailable", "available": False,
                        "candidate_count": 0, "collection_candidate_count": 0,
                        "document_candidate_count": 0,
                        "error": "query embedding or vector search failed",
                    }
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
        operational_channels = sum(
            1 for channel in channels.values()
            if channel["status"] in {"ok", "partial"}
        )
        status = (
            "unavailable" if operational_channels == 0
            else "partial" if degraded else "ok"
        )
        if vector_scores:
            retrieval = "hybrid"
        elif channels["lexical"]["available"]:
            retrieval = "lexical"
        elif channels["vector"]["available"]:
            retrieval = "vector"
        else:
            retrieval = "unavailable"
        return {
            "ok": operational_channels > 0,
            "status": status,
            "results": results,
            "degraded": sorted(set(degraded)),
            "channels": channels,
            "retrieval": retrieval,
            "reranked": False,
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
