from __future__ import annotations

import re
import hashlib
import math
import asyncio
import copy
import os
import time
from collections.abc import Sequence
from uuid import uuid5, NAMESPACE_URL
from .chunking import Chunker, chunk as chunk_text
from .embeddings import EmbeddingProvider, MAX_TEXTS as MAX_EMBEDDING_BATCH
from .models import Chunk, Document, DocumentIn, DocumentOut, SearchHit
from .store import DocumentStore
from .reranking import Reranker, RerankingDecorator, configured_ranking_provider
from .vector_store import VectorStore


def _retrieval_channel_timeout() -> float:
    try:
        configured = float(os.getenv("ENSEMBLE_RETRIEVAL_CHANNEL_TIMEOUT", "5"))
    except (TypeError, ValueError):
        configured = 5.0
    if not math.isfinite(configured):
        configured = 5.0
    return min(60.0, max(0.05, configured))


class DocumentService:
    def __init__(
        self, store: DocumentStore, chunker: Chunker, embeddings: EmbeddingProvider,
        vector_store: VectorStore | None = None, index_store: object | None = None,
    ):
        self.store = store
        self.chunker = chunker
        self.embeddings = embeddings
        self.vector_store = vector_store
        self.index_store = index_store

    async def _active_generation(self) -> str | None:
        if self.index_store is None:
            return None
        status = await self.index_store.generation_status()
        active_id = status.get("active_generation_id")
        active = next((item for item in status.get("generations", [])
                       if item.get("generation_id") == active_id), None)
        verification = (active or {}).get("verification") or {}
        if active and active.get("state") == "active" and verification.get("acknowledged_writes_verified") is True:
            return active_id
        return None

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
        if self.index_store is not None:
            await self.index_store.assert_generation_writes_open()
        requested_document_id = document.id
        document = await self.store.save(document, chunks)
        if document.id != requested_document_id:
            for chunk in chunks:
                chunk.document_id = document.id
                chunk.id = uuid5(NAMESPACE_URL, f"ensemble:{document.id}:{chunk.ordinal}")
            document = await self.store.save(document, chunks)
        active_generation_id = await self._active_generation()
        if active_generation_id is not None:
            await self.store.upsert_generation(
                active_generation_id, copy.deepcopy(document), copy.deepcopy(chunks),
            )
        vector_indexed = False
        digest = hashlib.sha256(request.content.encode("utf-8")).hexdigest()
        if vector_collection and self.vector_store is not None:
            if embed and self.embeddings.available and chunks and all(chunk.embedding for chunk in chunks):
                status = self.embeddings.status()
                model = status.get("model") or "unknown"
                dimensions = status.get("dimensions") or len(chunks[0].embedding)
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
                try:
                    for offset in range(0, len(items), 256):
                        await self.vector_store.upsert(
                            vector_collection, model, dimensions, items[offset:offset + 256]
                        )
                    vector_indexed = True
                except Exception:
                    # Base vector indexing is an optional retrieval channel.
                    # Keep document ingestion usable, but mirror the resulting
                    # durable base state into the active generation below.
                    vector_indexed = False

            # The durable document revision is authoritative. Never leave vectors
            # from an older revision searchable after a newer revision is accepted.
            try:
                await self.vector_store.delete_superseded(
                    vector_collection, document.source_id, digest
                )
            except Exception:
                vector_indexed = False

            if active_generation_id is not None:
                # Mirror what actually persisted in the base Vector store, not
                # the requested batch. A failed optional base write or cleanup
                # must not leave the active generation claiming a different
                # vector state. Fail the ingestion if this mirror fails so the
                # Index journal remains unacknowledged and the write is retryable.
                persisted = await self.vector_store.list_records_for_source(
                    vector_collection, document.source_id,
                )
                await self.vector_store.delete_generation_source(
                    active_generation_id, document.source_id, name=vector_collection,
                )
                if persisted:
                    collections = await self.vector_store.list_collections()
                    collection = next(
                        (item for item in collections if item["name"] == vector_collection),
                        None,
                    )
                    if collection is None:
                        raise RuntimeError(
                            f"Vector collection {vector_collection} disappeared during generation mirroring"
                        )
                    await self.vector_store.upsert_generation(
                        active_generation_id, vector_collection,
                        collection["model"], collection["dimensions"], persisted,
                    )
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
    def __init__(
        self, store: DocumentStore, embeddings: EmbeddingProvider,
        reranker: Reranker | None = None, vector_store: VectorStore | None = None,
        index_store: object | None = None,
    ):
        self.store = store
        self.embeddings = embeddings
        self.reranker = reranker
        self.vector_store = vector_store
        self.index_store = index_store
        provider = configured_ranking_provider(reranker)
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

        active_generation_id = None
        if self.index_store is not None:
            status_snapshot = await self.index_store.generation_status()
            active_id = status_snapshot.get("active_generation_id")
            active_row = next((item for item in status_snapshot.get("generations", [])
                               if item.get("generation_id") == active_id), None)
            verification = (active_row or {}).get("verification") or {}
            if active_row and active_row.get("state") == "active" and verification.get("acknowledged_writes_verified") is True:
                active_generation_id = active_id
        indexed = (
            await self.store.generation_all_chunks(active_generation_id)
            if active_generation_id is not None else await self.store.all_chunks()
        )
        lexical_scores: dict = {}
        vector_scores: dict = {}
        degraded: list[str] = []
        channels = {
            "lexical": {"status": "not_run", "available": False, "candidate_count": 0},
            "vector": {"status": "disabled", "available": False, "candidate_count": 0,
                       "collection_candidate_count": 0, "document_candidate_count": 0},
        }

        # Keep channels independent: one failing must not suppress the other.
        lexical_started = time.perf_counter()
        try:
            # Preserve the canonical FTS5/BM25 analyzer and scoring path.
            # Candidate IDs are intersected with the active generation below,
            # so stale or out-of-generation base rows cannot become results.
            lexical_matches = await asyncio.wait_for(
                self.store.lexical_search(
                    query, limit=min(2000, max(200, limit * 4)),
                ),
                timeout=_retrieval_channel_timeout(),
            )
            lexical_scores = {item.chunk.id: score for item, score in lexical_matches}
            channels["lexical"] = {
                "status": "ok", "available": True,
                "candidate_count": len(lexical_scores),
                "duration_ms": round((time.perf_counter() - lexical_started) * 1000, 3),
            }
        except Exception as exc:
            channels["lexical"] = {
                "status": "unavailable", "available": False,
                "candidate_count": 0, "error": "lexical search failed",
                "duration_ms": round((time.perf_counter() - lexical_started) * 1000, 3),
            }
            degraded.append("lexical_timeout" if isinstance(exc, TimeoutError) else "lexical")

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
                vector_started = time.perf_counter()
                embedding_ms = 0.0
                collection_ms = 0.0
                document_scan_ms = 0.0
                try:
                    embedding_started = time.perf_counter()
                    embedded = await asyncio.wait_for(
                        self.embeddings.embed([query], mode="query"),
                        timeout=_retrieval_channel_timeout(),
                    )
                    embedding_ms = round((time.perf_counter() - embedding_started) * 1000, 3)
                    if not isinstance(embedded, list) or len(embedded) != 1:
                        raise RuntimeError("embedding provider returned an invalid query vector")
                    query_vector = embedded[0]
                    if not isinstance(query_vector, (list, tuple)) or not query_vector:
                        raise RuntimeError("embedding provider returned an empty query vector")
                    if any(not math.isfinite(float(value)) for value in query_vector):
                        raise RuntimeError("embedding provider returned a non-finite query vector")

                    collection_scores: dict = {}
                    if self.vector_store is not None:
                        collection_started = time.perf_counter()
                        try:
                            vector_operation = (
                                self.vector_store.query_generation(
                                    active_generation_id, "transcript_text", query_vector, 200,
                                ) if active_generation_id is not None else
                                self.vector_store.query("transcript_text", query_vector, 200)
                            )
                            vector_hits = await asyncio.wait_for(
                                vector_operation, timeout=_retrieval_channel_timeout(),
                            )
                            known_ids = {str(item.chunk.id): item.chunk.id for item in indexed}
                            collection_scores = {
                                known_ids[hit["id"]]: float(hit["score"])
                                for hit in vector_hits
                                if hit.get("id") in known_ids
                                and math.isfinite(float(hit.get("score", 0.0)))
                                and float(hit.get("score", 0.0)) > 0
                            }
                        except Exception as exc:
                            collection_error = True
                            collection_timeout = isinstance(exc, TimeoutError)
                        finally:
                            collection_ms = round((time.perf_counter() - collection_started) * 1000, 3)

                    document_started = time.perf_counter()
                    document_scores: dict = {}
                    for item in indexed:
                        if item.chunk.embedding:
                            score = _cosine(query_vector, item.chunk.embedding)
                            if score > 0:
                                document_scores[item.chunk.id] = score
                    document_scan_ms = round((time.perf_counter() - document_started) * 1000, 3)
                    vector_scores = dict(collection_scores)
                    for identifier, score in document_scores.items():
                        vector_scores[identifier] = max(vector_scores.get(identifier, 0.0), score)

                    vector_status = "partial" if collection_error else "ok"
                    channels["vector"] = {
                        "status": vector_status, "available": True,
                        "candidate_count": len(vector_scores),
                        "collection_candidate_count": len(collection_scores),
                        "document_candidate_count": len(document_scores),
                        "duration_ms": round((time.perf_counter() - vector_started) * 1000, 3),
                        "embedding_ms": embedding_ms,
                        "collection_ms": collection_ms,
                        "document_scan_ms": document_scan_ms,
                    }
                    if collection_error:
                        channels["vector"]["error"] = (
                            "vector collection query timed out; document vectors remain available"
                            if collection_timeout else
                            "vector collection query failed; document vectors remain available"
                        )
                        degraded.append("vector_collection_timeout" if collection_timeout else "vector_collection")
                except Exception as exc:
                    channels["vector"] = {
                        "status": "unavailable", "available": False,
                        "candidate_count": 0, "collection_candidate_count": 0,
                        "document_candidate_count": 0,
                        "error": "query embedding or vector search failed",
                        "duration_ms": round((time.perf_counter() - vector_started) * 1000, 3),
                        "embedding_ms": embedding_ms,
                        "collection_ms": collection_ms,
                        "document_scan_ms": document_scan_ms,
                    }
                    degraded.append("embeddings_timeout" if isinstance(exc, TimeoutError) else "embeddings")

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
        if self.index_store is not None and hits:
            source_states = await self.index_store.get_sources(
                sorted({hit.source_id for hit in hits}),
            )
            current_hits = []
            for hit in hits:
                source_state = source_states.get(hit.source_id)
                if source_state is None:
                    continue
                if source_state.get("tombstoned") is True or source_state.get("purged") is True:
                    continue
                if source_state.get("high_sequence") != hit.revision:
                    continue
                current_hits.append(hit)
            hits = current_hits
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
