from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import random
import os
import math
import time
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.openapi.utils import get_openapi
from .chunking import Chunker, ChunkingError, PROFILES as CHUNKING_PROFILES, chunk as chunk_text, describe_profiles as describe_chunk_profiles
from .embeddings import EmbeddingProvider, MAX_BODY_BYTES as MAX_EMBED_BODY_BYTES, configured_embedding_provider, validate_embedding_request
from .models import DocumentIn, DocumentOut, SearchIn, SearchHit
from .services import DocumentService, SearchService
from .rest_client import InternalRestClient
from .store import DocumentStore, InMemoryDocumentStore, SqlDocumentStore
from .vector_store import SqliteVectorStore, VectorStore
from .vector_memory import InMemoryVectorStore
from .index_store import IndexStore, SqliteIndexStore
from .index_memory import InMemoryIndexStore
from .knowledge_store import KnowledgeStore, SqliteKnowledgeStore, InMemoryKnowledgeStore
from .graph_store import GraphStore, SqliteGraphStore
from .graph_memory import InMemoryGraphStore
from .tokenization import PROFILES as TOKENIZATION_PROFILES, TokenizationError, describe_profiles, tokenize
from .extraction import extract, normalize_entity
from .intent import RULES as INTENT_RULES, classify
from .ranking import DEFAULT_K, fuse
from .reranking import MODEL_ID as RERANK_MODEL_ID, Reranker, RerankingUnavailable, configured_ranking_provider, configured_reranker, validate_rerank_request
from .scraping import ScrapeError, preview_uri
from .transcripts import parse_jsonl, MAX_TRANSCRIPT_BYTES
from .transcript_store import TranscriptCheckpointStore, SqliteTranscriptCheckpointStore, InMemoryTranscriptCheckpointStore
from .file_ingestion import extract_pdf, PDFExtractionError, MAX_PDF_BYTES
from .memory_store import MemoryStore, SqliteMemoryStore, InMemoryMemoryStore
from .ga import GAConfig, GeneticAlgorithm, PARAMETERS as GA_PARAMETERS, SYSTEMS as GA_SYSTEMS
from .ga_store import GAStore, SqliteGAStore, InMemoryGAStore
from .thompson import select_model as thompson_select_model
from .thompson_store import ThompsonStore, SqliteThompsonStore, InMemoryThompsonStore
from .learning_store import LearningStore, SqliteLearningStore, InMemoryLearningStore
from .reclamation import ReclamationStore, SqliteReclamationStore, InMemoryReclamationStore
from .rebuild import IndexGenerationBuilder
from .config import settings_from_env, resolve_database_url
from .partitioning import partition_scope_middleware


def _validate_vector_collection_name(name: str) -> None:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name) or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="collection name must be 1-128 safe characters")


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


def _context_token_estimate(value: str) -> int:
    return (len(value) + 3) // 4


def _compress_context_text(value: str, query: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0:
        return "", bool(value)
    if len(value) <= max_chars:
        return value, False
    query_terms = {
        token.casefold() for token in re.findall(r"[^\W_]+", query)
        if len(token) > 1
    }
    sentences = [
        match.group(0).strip()
        for match in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)", value)
        if match.group(0).strip()
    ]
    ranked = []
    for index, sentence in enumerate(sentences):
        terms = {token.casefold() for token in re.findall(r"[^\W_]+", sentence)}
        ranked.append((len(query_terms & terms), index, sentence))
    selected, used = [], 0
    for score, index, sentence in sorted(ranked, key=lambda item: (-item[0], item[1])):
        extra = len(sentence) + (1 if selected else 0)
        if used + extra <= max_chars:
            selected.append((index, sentence))
            used += extra
    if selected:
        selected.sort(key=lambda item: item[0])
        return " ".join(sentence for _, sentence in selected), True
    return value[:max_chars].rstrip(), True


def _assemble_context(candidates: list[dict[str, Any]], query: str, max_tokens: int) -> dict[str, Any]:
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 32_000:
        raise ValueError("max_tokens must be an integer between 1 and 32000")
    included, blocks, seen = [], [], {}
    remaining, omitted, truncated, compressed = max_tokens, 0, 0, 0
    for candidate in candidates:
        value = candidate.get("content")
        if not isinstance(value, str) or not value.strip():
            omitted += 1
            continue
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        if digest in seen:
            previous = included[seen[digest]]
            alternate = {key: val for key, val in candidate.items() if key not in {"content", "score"}}
            previous.setdefault("also_from", []).append(alternate)
            omitted += 1
            continue
        source = candidate.get("source", "unknown")
        title = candidate.get("title") or candidate.get("section") or source
        header = f"[{source}] {title}"
        block = header + "\n" + value.strip()
        needed = _context_token_estimate(block)
        was_truncated = was_compressed = False
        if needed > remaining:
            available = remaining * 4 - len(header) - 1
            if available <= 0:
                omitted += 1
                continue
            selected, was_compressed = _compress_context_text(value.strip(), query, available)
            if not selected:
                omitted += 1
                continue
            block = header + "\n" + selected
            was_truncated = len(selected) < len(value.strip())
            needed = _context_token_estimate(block)
            if needed > remaining:
                omitted += 1
                continue
        item = {key: val for key, val in candidate.items() if key != "content"}
        item.update({
            "content": block, "estimated_tokens": needed,
            "truncated": was_truncated, "compressed": was_compressed,
            "content_sha256": digest,
        })
        seen[digest] = len(included)
        included.append(item)
        blocks.append(block)
        remaining -= needed
        truncated += int(was_truncated)
        compressed += int(was_compressed)
    return {
        "context": "\n\n".join(blocks), "sources": included,
        "estimated_tokens": max_tokens - remaining,
        "token_estimator": "UTF-8 character count divided by four; approximate, not model-tokenizer exact",
        "max_tokens": max_tokens, "omitted_count": omitted,
        "truncated_count": truncated, "compressed_count": compressed,
    }


def create_app(
    database_url: str | None = None,
    store: DocumentStore | None = None,
    reranker: Reranker | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
    index_store: IndexStore | None = None,
    knowledge_store: KnowledgeStore | None = None,
    graph_store: GraphStore | None = None,
    memory_store: MemoryStore | None = None,
    ga_store: GAStore | None = None,
    thompson_store: ThompsonStore | None = None,
    learning_store: LearningStore | None = None,
    transcript_store: TranscriptCheckpointStore | None = None,
) -> FastAPI:
    database_url = resolve_database_url(database_url)
    document_store = store or SqlDocumentStore(database_url)
    if isinstance(document_store, InMemoryDocumentStore) and os.getenv("ENSEMBLE_PARTITION_TOKENS", "").strip():
        raise ValueError("partition authentication requires durable SQLite-backed stores; in-memory adapters are test-only")
    embeddings = embedding_provider or configured_embedding_provider()
    active_reranker = reranker or configured_reranker()
    active_vector_store = vector_store or (
        InMemoryVectorStore() if isinstance(document_store, InMemoryDocumentStore)
        else SqliteVectorStore(database_url)
    )
    active_index_store = index_store or (
        InMemoryIndexStore() if isinstance(document_store, InMemoryDocumentStore)
        else SqliteIndexStore(database_url)
    )
    active_knowledge_store = knowledge_store or (
        InMemoryKnowledgeStore() if isinstance(document_store, InMemoryDocumentStore)
        else SqliteKnowledgeStore(database_url)
    )
    active_graph_store = graph_store or (
        InMemoryGraphStore() if isinstance(document_store, InMemoryDocumentStore)
        else SqliteGraphStore(database_url)
    )
    active_memory_store = memory_store or (
        InMemoryMemoryStore() if isinstance(document_store, InMemoryDocumentStore)
        else SqliteMemoryStore(database_url)
    )
    active_ga_store = ga_store or (
        InMemoryGAStore() if isinstance(document_store, InMemoryDocumentStore)
        else SqliteGAStore(database_url)
    )
    active_thompson_store = thompson_store or (
        InMemoryThompsonStore() if isinstance(document_store, InMemoryDocumentStore)
        else SqliteThompsonStore(database_url)
    )
    thompson_rng = random.Random()
    active_learning_store = learning_store or (
        InMemoryLearningStore() if isinstance(document_store, InMemoryDocumentStore)
        else SqliteLearningStore(database_url)
    )
    active_transcript_store = transcript_store or (
        InMemoryTranscriptCheckpointStore() if isinstance(document_store, InMemoryDocumentStore)
        else SqliteTranscriptCheckpointStore(database_url)
    )

    active_reclamation_store = (
        InMemoryReclamationStore() if isinstance(document_store, InMemoryDocumentStore)
        else SqliteReclamationStore(database_url)
    )

    async def reclaim_due_sources() -> int:
        jobs = await active_reclamation_store.claim_due(100)
        for job in jobs:
            source_id = job["source_id"]
            try:
                # The authoritative Index tombstone is retained. Only active
                # materialized data is reclaimed; generation tombstones remain.
                reclaimed = [
                    await document_store.reclaim_source(source_id),
                    await active_knowledge_store.reclaim_source(source_id),
                    await active_vector_store.reclaim_source(source_id),
                ]
                if any(count > 500 for count in reclaimed):
                    # A full page means there may be more rows. Retry the
                    # idempotent source cleanup in another bounded pass.
                    await active_reclamation_store.retry(source_id, "additional reclamation pages remain")
                else:
                    await active_reclamation_store.complete(source_id)
            except Exception as exc:
                await active_reclamation_store.retry(source_id, f"{type(exc).__name__}: {exc}")
        return len(jobs)

    async def reclamation_worker() -> None:
        while True:
            try:
                processed = await reclaim_due_sources()
                await asyncio.sleep(1 if processed else 15)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A transient queue/database failure must not permanently stop
                # reclamation for this application process.
                await asyncio.sleep(15)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await document_store.initialize()
            await active_vector_store.initialize()
            await active_index_store.initialize()
            recover_fence = getattr(active_index_store, "recover_generation_write_fence", None)
            if callable(recover_fence):
                await recover_fence()
            await active_knowledge_store.initialize()
            await active_graph_store.initialize()
            await active_memory_store.initialize()
            await active_ga_store.initialize()
            await active_ga_store.recover_interrupted_runs()
            await active_thompson_store.initialize()
            await active_learning_store.initialize()
            await active_transcript_store.initialize()
            await active_transcript_store.recover_interrupted()
            await active_reclamation_store.initialize()
            worker_task = asyncio.create_task(reclamation_worker())
            try:
                yield
            finally:
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass
                await active_reclamation_store.close()
                await active_transcript_store.close()
        finally:
            await active_learning_store.close()
            await active_thompson_store.close()
            await active_ga_store.close()
            await active_memory_store.close()
            await active_graph_store.close()
            await active_knowledge_store.close()
            await active_index_store.close()
            await active_vector_store.close()
            await document_store.close()

    app = FastAPI(
        title="Ensemble API",
        version="0.1",
        description="REST-first memory, knowledge, and retrieval platform.",
        lifespan=lifespan,
    )
    app.middleware("http")(partition_scope_middleware)

    def openapi_with_partition_auth():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title, version=app.version,
            description=app.description, routes=app.routes,
        )
        schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        schemes["BearerAuth"] = {"type": "http", "scheme": "bearer", "bearerFormat": "opaque"}
        for path, methods in schema.get("paths", {}).items():
            if path.startswith("/api/v1/"):
                for operation in methods.values():
                    if isinstance(operation, dict) and "responses" in operation:
                        operation["security"] = [{"BearerAuth": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = openapi_with_partition_auth
    documents = DocumentService(
        document_store, Chunker(), embeddings, active_vector_store, active_index_store,
    )
    search = SearchService(
        document_store, embeddings, active_reranker, active_vector_store, active_index_store,
    )
    ranking_provider = configured_ranking_provider(active_reranker)

    async def active_generation_id() -> str | None:
        status = await active_index_store.generation_status()
        generation_id = status.get("active_generation_id")
        active = next((item for item in status.get("generations", [])
                       if item.get("generation_id") == generation_id), None)
        verification = (active or {}).get("verification") or {}
        if active and active.get("state") == "active" and verification.get("acknowledged_writes_verified") is True:
            return generation_id
        return None

    async def index_document_entities(
        source_id: str, revision: int, content: str, *,
        origin: str, locator: str, request: Request | None = None,
    ) -> int:
        """Associate exact extracted entities with a durable source revision through REST."""
        internal = InternalRestClient(app, {
            "entities": os.getenv("ENSEMBLE_ENTITIES_REST_URL"),
            "graph": os.getenv("ENSEMBLE_GRAPH_REST_URL"),
        })
        identity = hashlib.sha256(f"{source_id}:{revision}".encode("utf-8")).hexdigest()
        source_response = await internal.request(
            "POST", "/api/v1/graph/nodes", request=request,
            json={
                "type": "source",
                "id": "source_" + identity,
                "properties": {
                    "source_id": source_id, "revision": revision,
                    "origin": origin, "locator": locator,
                },
            },
        )
        source_node = source_response["node"]
        extracted = await internal.request(
            "POST", "/api/v1/entities/extract", request=request,
            json={"text": content[:1_000_000], "source": "ingestion"},
        )
        linked = 0
        for candidate in extracted.get("entities", [])[:500]:
            if not candidate.get("exact"):
                continue
            kind, normalized = candidate["type"], candidate["normalized"]
            matches = (await internal.request(
                "POST", "/api/v1/graph/query", request=request,
                json={"type": "entity", "properties": {
                    "entity_type": kind, "normalized": normalized,
                }},
            )).get("nodes", [])
            if len(matches) > 1:
                continue
            if matches:
                entity_node = matches[0]
            else:
                entity_identity = hashlib.sha256(f"{kind}:{normalized}".encode("utf-8")).hexdigest()
                entity_node = (await internal.request(
                    "POST", "/api/v1/graph/nodes", request=request,
                    json={
                        "type": "entity", "id": "entity_" + entity_identity,
                        "properties": {
                            "entity_type": kind, "normalized": normalized,
                            "canonical_name": normalized,
                        },
                    },
                ))["node"]
            edge_identity = hashlib.sha256(
                f"{source_id}:{revision}:{entity_node['id']}:{candidate['start']}:{candidate['end']}".encode("utf-8")
            ).hexdigest()
            await internal.request(
                "POST", "/api/v1/graph/edges", request=request,
                json={
                    "source": source_node["id"], "target": entity_node["id"],
                    "type": "mentions", "id": "mention_" + edge_identity,
                    "properties": {
                        "source_id": source_id, "revision": revision,
                        "start": candidate["start"], "end": candidate["end"],
                    },
                },
            )
            linked += 1
        return linked

    async def ingest_indexed_document(
        document: DocumentIn, *, embed: bool = True, vector_collection: str | None = None,
        origin: str = "document", locator: str | None = None,
        request: Request | None = None,
    ):
        internal = InternalRestClient(app, {
            "index": os.getenv("ENSEMBLE_INDEX_REST_URL"),
            "documents": os.getenv("ENSEMBLE_DOCUMENTS_REST_URL"),
            "entities": os.getenv("ENSEMBLE_ENTITIES_REST_URL"),
            "graph": os.getenv("ENSEMBLE_GRAPH_REST_URL"),
        })
        revision_id = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
        profiles = {"chunking": document.chunk_profile, "media_type": document.media_type}
        decision = await internal.request(
            "POST", "/api/v1/index/revisions", request=request,
            json={
                "source_id": document.source_id,
                "revision_sequence": document.revision,
                "revision_id": revision_id,
                "profiles": profiles,
            },
        )
        if decision["decision"] not in {"accepted", "duplicate"}:
            raise HTTPException(
                status_code=409,
                detail="source revision rejected: " + decision["decision"],
            )
        result = await internal.request(
            "POST", "/api/v1/documents/records", request=request,
            json={
                "document": document.model_dump(mode="json"),
                "embed": embed,
                "vector_collection": vector_collection,
            },
        )
        result = DocumentOut.model_validate(result)
        try:
            linked = await index_document_entities(
                document.source_id, document.revision, document.content,
                origin=origin, locator=locator or document.source_id, request=request,
            )
            result.metadata = {**result.metadata, "graph_entities_indexed": linked}
        except Exception:
            result.metadata = {**result.metadata, "graph_entities_indexed": 0, "graph_degraded": True}
        await active_index_store.acknowledge_generation_change(
            document.source_id, document.revision, False,
        )
        return result, decision["decision"]

    async def expand_graph_results(query: str, entities: list[dict[str, Any]], results: list[SearchHit], limit: int, internal: InternalRestClient, request: Request):
        graph_sources: dict[str, int] = {}
        graph_matches = []
        try:
            for candidate in entities:
                if not candidate.get("exact"):
                    continue
                matches = (await internal.request(
                    "POST", "/api/v1/graph/query", request=request,
                    json={"type": "entity", "properties": {
                        "entity_type": candidate["type"], "normalized": candidate["normalized"],
                    }},
                )).get("nodes", [])
                if len(matches) != 1:
                    continue
                entity_node = matches[0]
                graph_matches.append({
                    "entity_id": entity_node["id"],
                    "type": candidate["type"],
                    "normalized": candidate["normalized"],
                })
                related = (await internal.request(
                    "POST", "/api/v1/graph/traverse", request=request,
                    json={"start": entity_node["id"], "direction": "in",
                          "max_depth": 1, "edge_type": "mentions"},
                )).get("results", [])
                for item in related:
                    source_node = item["node"]
                    if source_node.get("type") != "source":
                        continue
                    properties = source_node.get("properties") or {}
                    source_id, revision = properties.get("source_id"), properties.get("revision")
                    if isinstance(source_id, str) and isinstance(revision, int):
                        graph_sources[source_id] = max(graph_sources.get(source_id, 0), revision)

            if not graph_sources:
                return results, {"matched_entities": graph_matches, "candidate_sources": 0}

            source_ids = sorted(graph_sources)
            source_states: dict[str, dict[str, Any]] = {}
            by_source: dict[str, list[dict[str, Any]]] = {}
            for offset in range(0, len(source_ids), 200):
                batch = source_ids[offset:offset + 200]
                source_state_response = await internal.request(
                    "POST", "/api/v1/index/sources/query", request=request,
                    json={"source_ids": batch},
                )
                chunk_response = await internal.request(
                    "POST", "/api/v1/documents/chunks/query", request=request,
                    json={"source_ids": batch},
                )
                source_states.update(source_state_response.get("sources", {}))
                for chunk in chunk_response.get("chunks", []):
                    by_source.setdefault(chunk["source_id"], []).append(chunk)
            existing = {
                (hit.source_id, str(hit.chunk_id)): index
                for index, hit in enumerate(results)
            }
            expanded = list(results)
            for source_rank, source_id in enumerate(source_ids, start=1):
                state = source_states.get(source_id)
                if state is None or state.get("tombstoned") or state.get("high_sequence") != graph_sources[source_id]:
                    continue
                contribution = 1.0 / (60 + source_rank)
                for item in by_source.get(source_id, []):
                    key = (source_id, str(item["chunk_id"]))
                    if key in existing:
                        position = existing[key]
                        hit = expanded[position]
                        expanded[position] = hit.model_copy(update={
                            "graph_score": hit.graph_score + contribution,
                            "fusion_score": hit.fusion_score + contribution,
                        })
                        continue
                    expanded.append(SearchHit(
                        document_id=item["document_id"], source_id=source_id,
                        revision=item["revision"], title=item["title"],
                        chunk_id=item["chunk_id"], chunk_ordinal=item["chunk_ordinal"],
                        text=item["text"], lexical_score=0.0, vector_score=0.0,
                        graph_score=contribution, fusion_score=contribution,
                        chunk_profile=item["chunk_profile"], chunk_start=item["chunk_start"],
                        chunk_end=item["chunk_end"], chunk_header=item["chunk_header"],
                        session_id=item.get("session_id"), project=item.get("project"),
                        role=item.get("role"), kind=item.get("kind"),
                        is_subagent=item.get("is_subagent"), text_sha=item["text_sha"],
                    ))
            expanded.sort(
                key=lambda hit: (hit.fusion_score, hit.lexical_score, hit.graph_score, str(hit.chunk_id)),
                reverse=True,
            )
            return expanded[:limit], {
                "matched_entities": graph_matches, "candidate_sources": len(graph_sources),
            }
        except Exception:
            return results, {
                "matched_entities": graph_matches, "candidate_sources": 0, "degraded": True,
            }

    async def tombstone_indexed_source(
        source_id: str, request: Request | None = None,
    ) -> dict[str, Any]:
        # The Index tombstone is authoritative before cleanup begins. Cleanup
        # is best-effort: one failed capability must not prevent the remaining
        # stores and vector collections from being pruned.
        internal = InternalRestClient(app, {
            "documents": os.getenv("ENSEMBLE_DOCUMENTS_REST_URL"),
            "knowledge": os.getenv("ENSEMBLE_KNOWLEDGE_REST_URL"),
            "vector": os.getenv("ENSEMBLE_VECTOR_REST_URL"),
        })
        cleanup: dict[str, Any] = {
            "status": "complete", "retryable": False, "completed": [],
            "failed": [],
        }
        for name, path in (
            ("documents", "/api/v1/documents/sources/tombstone"),
            ("knowledge", "/api/v1/knowledge/sources/tombstone"),
        ):
            try:
                await internal.request(
                    "POST", path, request=request, json={"source_id": source_id},
                )
                cleanup["completed"].append(name)
            except Exception as exc:
                cleanup["failed"].append({
                    "target": name, "error": type(exc).__name__,
                })
        try:
            response = await internal.request(
                "GET", "/api/v1/vector/collections", request=request,
            )
            collections = response.get("collections", [])
        except Exception as exc:
            collections = []
            cleanup["failed"].append({
                "target": "vector_collections", "error": type(exc).__name__,
            })
        for collection in collections:
            name = collection["name"]
            try:
                response = await internal.request(
                    "POST",
                    f"/api/v1/vector/collections/{name}/sources/tombstone",
                    request=request, json={"source_id": source_id},
                )
                cleanup["completed"].append({
                    "target": "vector:" + name,
                    "deleted": response.get("deleted") if isinstance(response, dict) else None,
                })
            except Exception as exc:
                cleanup["failed"].append({
                    "target": "vector:" + name, "error": type(exc).__name__,
                })
        if not cleanup["failed"]:
            active_generation = await active_generation_id()
            source = await active_index_store.get_source(source_id)
            if active_generation is not None and source is not None:
                try:
                    await document_store.tombstone_generation_source(active_generation, source_id)
                    await active_knowledge_store.tombstone_generation_source(active_generation, source_id)
                    await active_vector_store.tombstone_generation_source(active_generation, source_id)
                    await active_index_store.acknowledge_generation_change(
                        source_id, source["high_sequence"], True,
                    )
                    cleanup["completed"].append("active_generation")
                except Exception as exc:
                    cleanup["failed"].append({
                        "target": "active_generation", "error": type(exc).__name__,
                    })
        if cleanup["failed"]:
            cleanup["status"] = "partial"
            cleanup["retryable"] = True
        return cleanup

    app.state.store = document_store
    app.state.embedding_provider = embeddings
    app.state.reranker = active_reranker
    app.state.vector_store = active_vector_store
    app.state.index_store = active_index_store
    app.state.knowledge_store = active_knowledge_store
    app.state.graph_store = active_graph_store
    app.state.memory_store = active_memory_store
    app.state.ga_store = active_ga_store
    app.state.thompson_store = active_thompson_store
    app.state.learning_store = active_learning_store
    app.state.transcript_store = active_transcript_store
    app.state.settings = settings_from_env(database_url)

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

    @app.get("/api/v1/system/config", tags=["system"])
    async def system_config():
        settings = app.state.settings
        return {
            "ok": True,
            "listener": {"host": settings.host, "port": settings.port},
            "persistence": {"mode": "durable", "configured": True},
        }

    @app.get("/health", tags=["operations"])
    async def health():
        return {"status": "ok", "service": "ensemble"}

    @app.get("/ready", tags=["operations"])
    async def ready():
        try:
            await document_store.check_ready()
            await active_memory_store.check_ready()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="persistence unavailable") from exc
        return {
            "status": "ready",
            "persistence": "durable",
            "embeddings": embeddings.available,
            "capabilities": ["documents", "search", "lexical", "hybrid", "chunking", "embeddings", "vector", "index", "knowledge", "tokenization", "intent", "ranking", "reranking", "graph", "entities", "memory", "context", "ga", "thompson", "learning"],
        }

    @app.get("/api/v1/embeddings/health", tags=["embeddings"])
    async def embeddings_health():
        status = embeddings.status()
        return JSONResponse(status_code=200 if embeddings.available else 503, content=status)

    @app.get("/api/v1/embeddings/model", tags=["embeddings"])
    async def embeddings_model():
        status = embeddings.status()
        return JSONResponse(status_code=200 if embeddings.available else 503, content=status)

    @app.post("/api/v1/embeddings/embed", tags=["embeddings"])
    async def embed_texts(payload: dict[str, Any], request: Request):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_EMBED_BODY_BYTES:
            raise HTTPException(status_code=413, detail="request body exceeds 1 MiB")
        texts = payload.get("texts")
        mode = payload.get("mode", "document")
        try:
            validate_embedding_request(texts, mode)
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not embeddings.available:
            raise HTTPException(status_code=503, detail=embeddings.status().get("error", "embedding provider unavailable"))
        try:
            vectors = await embeddings.embed(texts, mode=mode)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="embedding provider failed") from exc
        status = embeddings.status()
        dimensions = status.get("dimensions")
        if dimensions is None and vectors:
            dimensions = len(vectors[0])
        return {
            "ok": True,
            "model": status.get("model"),
            "dimensions": dimensions,
            "embeddings": vectors,
        }

    @app.get("/api/v1/knowledge/health", tags=["knowledge"])
    async def knowledge_health():
        return {"ok": True, "service": "knowledge"}

    @app.get("/api/v1/knowledge/ready", tags=["knowledge"])
    async def knowledge_ready():
        try:
            await active_knowledge_store.get("__readiness_probe__")
            await active_index_store.list_sources(1)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="knowledge or index persistence unavailable") from exc
        return {"ok": True, "ready": True, "index": "available"}

    @app.post("/api/v1/knowledge/records", tags=["knowledge"])
    async def knowledge_put(payload: dict[str, Any], request: Request):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 8 * 1_048_576:
            raise HTTPException(status_code=413, detail="request body exceeds 8 MiB")
        source_id = payload.get("source_id")
        revision_id = payload.get("revision_id")
        sequence = payload.get("revision_sequence")
        title, content = payload.get("title"), payload.get("content")
        provenance, profiles = payload.get("provenance"), payload.get("profiles")
        if not isinstance(source_id, str) or not source_id or len(source_id) > 512:
            raise HTTPException(status_code=400, detail="source_id must be a non-empty string of at most 512 characters")
        if not isinstance(revision_id, str) or not revision_id or len(revision_id) > 512:
            raise HTTPException(status_code=400, detail="revision_id must be a non-empty string of at most 512 characters")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise HTTPException(status_code=400, detail="revision_sequence must be a positive integer")
        if not isinstance(title, str) or not title or len(title) > 2048:
            raise HTTPException(status_code=400, detail="title must be a non-empty string of at most 2048 characters")
        if not isinstance(content, str) or not content:
            raise HTTPException(status_code=400, detail="content must be a non-empty string")
        if len(content) > 4_000_000:
            raise HTTPException(status_code=413, detail="content is limited to 4000000 characters")
        if not isinstance(provenance, dict) or not isinstance(provenance.get("origin"), str) or not isinstance(provenance.get("locator"), str):
            raise HTTPException(status_code=400, detail="provenance must include string origin and locator")
        if not isinstance(profiles, dict) or not profiles:
            raise HTTPException(status_code=400, detail="profiles must be a non-empty object")
        decision = await active_index_store.record_revision(source_id, sequence, revision_id, profiles)
        if decision["decision"] not in {"accepted", "duplicate"}:
            return {"ok": True, "decision": decision["decision"], "source_id": source_id, "stored": False}
        chunk_profile = profiles.get("chunking", "window-v1")
        if isinstance(chunk_profile, str) and "@" in chunk_profile:
            chunk_profile = chunk_profile.split("@", 1)[0]
        media_type = profiles.get("media_type", "text/plain")
        document = DocumentIn(
            source_id=source_id, revision=sequence, title=title, content=content,
            chunk_profile=chunk_profile, media_type=media_type,
            metadata={
                "origin": provenance["origin"], "locator": provenance["locator"],
                "revision_id": revision_id, "profiles": profiles,
            },
        )
        try:
            indexed = await documents.ingest(
                document, vector_collection="knowledge_chunks"
            )
            digest = await active_knowledge_store.put({
                "source_id": source_id,
                "revision_sequence": sequence,
                "revision_id": revision_id,
                "title": title,
                "content": content,
                "origin": provenance["origin"],
                "locator": provenance["locator"],
                "profiles": profiles,
            })
            active_generation = await active_generation_id()
            if active_generation is not None:
                knowledge_record = await active_knowledge_store.get(source_id)
                if knowledge_record is None:
                    raise RuntimeError("Knowledge record was not readable after publication")
                await active_knowledge_store.upsert_generation(active_generation, knowledge_record)
            await active_index_store.acknowledge_generation_change(source_id, sequence, False)
        except ChunkingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            graph_entities = await index_document_entities(
                source_id, sequence, content,
                origin=provenance["origin"], locator=provenance["locator"],
                request=request,
            )
        except Exception:
            graph_entities = 0
        return {
            "ok": True, "decision": decision["decision"], "source_id": source_id,
            "stored": True, "content_sha256": digest,
            "document": indexed, "vector_indexed": indexed.vector_indexed,
            "graph_entities_indexed": graph_entities,
        }

    @app.get("/api/v1/knowledge/records/{source_id}", tags=["knowledge"])
    async def knowledge_get(source_id: str):
        record = await active_knowledge_store.get(source_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown record")
        return {"ok": True, "record": record}

    @app.post("/api/v1/knowledge/sources/tombstone", tags=["knowledge"])
    async def knowledge_source_tombstone(payload: dict[str, Any]):
        source_id = payload.get("source_id")
        if not isinstance(source_id, str) or not source_id or len(source_id) > 512:
            raise HTTPException(status_code=400, detail="source_id must be a non-empty string of at most 512 characters")
        try:
            await active_index_store.assert_generation_writes_open()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await active_knowledge_store.tombstone(source_id)
        active_generation = await active_generation_id()
        if active_generation is not None:
            await active_knowledge_store.tombstone_generation_source(active_generation, source_id)
        return {"ok": True, "source_id": source_id}

    @app.post("/api/v1/knowledge/records/{source_id}/tombstone", tags=["knowledge"])
    async def knowledge_tombstone(source_id: str, payload: dict[str, Any], request: Request):
        sequence = payload.get("revision_sequence")
        if not source_id or len(source_id) > 512:
            raise HTTPException(status_code=400, detail="source_id must be a non-empty string of at most 512 characters")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise HTTPException(status_code=400, detail="revision_sequence must be a positive integer")
        internal = InternalRestClient(app, {"index": os.getenv("ENSEMBLE_INDEX_REST_URL")})
        return await internal.request(
            "POST", "/api/v1/index/tombstones", request=request,
            json={"source_id": source_id, "revision_sequence": sequence},
        )

    @app.post("/api/v1/documents/sources/tombstone", tags=["documents"])
    async def documents_source_tombstone(payload: dict[str, Any]):
        source_id = payload.get("source_id")
        if not isinstance(source_id, str) or not source_id or len(source_id) > 512:
            raise HTTPException(status_code=400, detail="source_id must be a non-empty string of at most 512 characters")
        try:
            await active_index_store.assert_generation_writes_open()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await document_store.tombstone_source(source_id)
        active_generation = await active_generation_id()
        if active_generation is not None:
            await document_store.tombstone_generation_source(active_generation, source_id)
        return {"ok": True, "source_id": source_id}

    @app.post("/api/v1/documents/records", tags=["documents"])
    async def documents_ingest(payload: dict[str, Any]):
        try:
            document = DocumentIn.model_validate(payload.get("document", {}))
            result = await documents.ingest(
                document,
                embed=payload.get("embed", True),
                vector_collection=payload.get("vector_collection"),
            )
            return result
        except ChunkingError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    generation_builder = IndexGenerationBuilder(
        active_index_store, document_store, active_vector_store,
        active_knowledge_store, active_memory_store,
    )

    @app.post("/api/v1/index/generations", tags=["index"])
    async def index_generation_create(payload: dict[str, Any]):
        generation_id = payload.get("generation_id") or uuid4().hex
        if not isinstance(generation_id, str) or not generation_id or len(generation_id) > 64:
            raise HTTPException(status_code=400, detail="generation_id must contain 1-64 characters")
        try:
            generation = await active_index_store.create_generation(generation_id)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "generation": generation}

    @app.get("/api/v1/index/generations", tags=["index"])
    async def index_generation_status():
        status = await active_index_store.generation_status()
        building = next((item for item in status["generations"] if item["state"] == "building"), None)
        checkpoints = (building or {}).get("checkpoints") or {}
        required = (
            "index", "documents", "vectors", "memory", "knowledge",
            "index_reconciliation", "documents_reconciliation",
            "vectors_reconciliation", "memory_reconciliation",
            "knowledge_reconciliation",
        )
        missing = [name for name in required if (checkpoints.get(name) or {}).get("sealed") is not True]
        return {
            "ok": True, **status,
            "build": {
                "generation_id": (building or {}).get("generation_id"),
                "required_checkpoints": list(required),
                "missing_checkpoints": missing,
                "activation_ready": building is not None and not missing,
                "blocked_capabilities": sorted({
                    name.removesuffix("_reconciliation") for name in missing
                }),
            },
        }

    @app.post("/api/v1/index/generations/{generation_id}/copy/index", tags=["index"])
    async def index_generation_copy_index(generation_id: str, payload: dict[str, Any]):
        try:
            return {"ok": True, **await generation_builder.copy_index_sources(
                generation_id, page_size=payload.get("page_size", 500),
            )}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/index/generations/{generation_id}/copy/documents", tags=["index"])
    async def index_generation_copy_documents(generation_id: str, payload: dict[str, Any]):
        try:
            return {"ok": True, **await generation_builder.copy_documents_page(
                generation_id, page_size=payload.get("page_size", 500),
            )}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/index/generations/{generation_id}/copy/vectors", tags=["index"])
    async def index_generation_copy_vectors(generation_id: str, payload: dict[str, Any]):
        try:
            return {"ok": True, **await generation_builder.copy_vectors_page(
                generation_id, page_size=payload.get("page_size", 500),
            )}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/index/generations/{generation_id}/copy/memory", tags=["index"])
    async def index_generation_copy_memory(generation_id: str, payload: dict[str, Any]):
        try:
            return {"ok": True, **await generation_builder.copy_memory_page(
                generation_id, page_size=payload.get("page_size", 500),
            )}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/index/generations/{generation_id}/copy/knowledge", tags=["index"])
    async def index_generation_copy_knowledge(generation_id: str, payload: dict[str, Any]):
        try:
            return {"ok": True, **await generation_builder.copy_knowledge_page(
                generation_id, page_size=payload.get("page_size", 500),
            )}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/index/generations/{generation_id}/reconcile/index", tags=["index"])
    async def index_generation_reconcile_index(generation_id: str, payload: dict[str, Any]):
        try:
            return {"ok": True, **await generation_builder.reconcile_index_changes_page(
                generation_id, page_size=payload.get("page_size", 250),
            )}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/index/generations/{generation_id}/reconcile/documents", tags=["index"])
    async def index_generation_reconcile_documents(generation_id: str, payload: dict[str, Any]):
        try:
            return {"ok": True, **await generation_builder.reconcile_document_changes_page(
                generation_id, page_size=payload.get("page_size", 250),
            )}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/index/generations/{generation_id}/reconcile/vectors", tags=["index"])
    async def index_generation_reconcile_vectors(generation_id: str, payload: dict[str, Any]):
        try:
            return {"ok": True, **await generation_builder.reconcile_vector_changes_page(
                generation_id, page_size=payload.get("page_size", 250),
            )}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/index/generations/{generation_id}/reconcile/memory", tags=["index"])
    async def index_generation_reconcile_memory(generation_id: str, payload: dict[str, Any]):
        try:
            return {"ok": True, **await generation_builder.reconcile_memory_changes_page(
                generation_id, page_size=payload.get("page_size", 250),
            )}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/index/generations/{generation_id}/reconcile/knowledge", tags=["index"])
    async def index_generation_reconcile_knowledge(generation_id: str, payload: dict[str, Any]):
        try:
            return {"ok": True, **await generation_builder.reconcile_knowledge_changes_page(
                generation_id, page_size=payload.get("page_size", 250),
            )}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/index/generations/{generation_id}/fence", tags=["index"])
    async def index_generation_fence(generation_id: str):
        try:
            return {"ok": True, **await generation_builder.fence_reconciled_generation(generation_id)}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/index/generations/{generation_id}/activate", tags=["index"])
    async def index_generation_activate(generation_id: str):
        status = await active_index_store.generation_status()
        target = next((item for item in status["generations"] if item["generation_id"] == generation_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="unknown generation")
        if target["state"] == "active" and status["active_generation_id"] == generation_id:
            return {
                "ok": True, "active_generation_id": generation_id,
                "epoch": status["epoch"], "already_active": True,
            }
        if target["state"] != "building":
            raise HTTPException(status_code=404, detail="unknown building generation")
        checkpoints = target.get("checkpoints") or {}
        required = (
            "index", "documents", "vectors", "memory", "knowledge",
            "index_reconciliation", "documents_reconciliation",
            "vectors_reconciliation", "memory_reconciliation",
            "knowledge_reconciliation",
        )
        missing = [name for name in required if (checkpoints.get(name) or {}).get("sealed") is not True]
        if missing:
            raise HTTPException(
                status_code=409,
                detail={"message": "generation is not activation-ready", "missing_checkpoints": missing},
            )
        high_watermark = await active_index_store.generation_change_high_watermark(generation_id)
        verification = {
            "acknowledged_writes_verified": True,
            "tombstones_verified": True,
            "reconciled_through_change_id": high_watermark,
        }
        try:
            activated = await active_index_store.activate_generation(
                generation_id, status["active_generation_id"], status["epoch"], verification,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, **activated}

    @app.post("/api/v1/index/generations/{generation_id}/abort", tags=["index"])
    async def index_generation_abort(generation_id: str, payload: dict[str, Any]):
        reason = payload.get("reason")
        try:
            result = await active_index_store.abort_generation(generation_id, reason)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "generation": result}

    @app.get("/api/v1/index/health", tags=["index"])
    async def index_health():
        return {"ok": True, "service": "index"}

    @app.get("/api/v1/index/ready", tags=["index"])
    async def index_ready():
        try:
            await active_index_store.list_sources(1)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="index persistence unavailable") from exc
        return {"ok": True, "ready": True}

    @app.post("/api/v1/index/revisions", tags=["index"])
    async def index_revision(payload: dict[str, Any]):
        source_id = payload.get("source_id")
        sequence = payload.get("revision_sequence")
        revision_id = payload.get("revision_id")
        profiles = payload.get("profiles")
        if not isinstance(source_id, str) or not source_id or len(source_id) > 512:
            raise HTTPException(status_code=400, detail="source_id must be a non-empty string of at most 512 characters")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise HTTPException(status_code=400, detail="revision_sequence must be a positive integer")
        if not isinstance(revision_id, str) or not revision_id or len(revision_id) > 512:
            raise HTTPException(status_code=400, detail="revision_id must be a non-empty string of at most 512 characters")
        if not isinstance(profiles, dict) or not profiles:
            raise HTTPException(status_code=400, detail="profiles must be a non-empty object")
        result = await active_index_store.record_revision(source_id, sequence, revision_id, profiles)
        return {"ok": True, **result}

    @app.post("/api/v1/index/tombstones", tags=["index"])
    async def index_tombstone(payload: dict[str, Any], request: Request):
        source_id = payload.get("source_id")
        sequence = payload.get("revision_sequence")
        if not isinstance(source_id, str) or not source_id or len(source_id) > 512:
            raise HTTPException(status_code=400, detail="source_id must be a non-empty string of at most 512 characters")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise HTTPException(status_code=400, detail="revision_sequence must be a positive integer")
        result = await active_index_store.record_tombstone(source_id, sequence)
        if result["decision"] == "tombstoned":
            cleanup = await tombstone_indexed_source(source_id, request)
        else:
            cleanup = {"status": "not_required", "retryable": False, "completed": [], "failed": []}
        return {"ok": True, **result, "cleanup": cleanup}

    @app.get("/api/v1/index/sources/{source_id}", tags=["index"])
    async def index_source(source_id: str):
        source = await active_index_store.get_source(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="unknown source")
        return {"ok": True, "source": source}

    @app.get("/api/v1/index/manifest", tags=["index"])
    async def index_manifest(limit: int = 100):
        if not 1 <= limit <= 500:
            raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
        return {"ok": True, "sources": await active_index_store.list_sources(limit)}

    @app.post("/api/v1/index/sources/query", tags=["index"])
    async def index_sources_query(payload: dict[str, Any]):
        source_ids = payload.get("source_ids")
        if (not isinstance(source_ids, list) or not source_ids
                or len(source_ids) > 200
                or any(not isinstance(value, str) or not value or len(value) > 2048
                       for value in source_ids)):
            raise HTTPException(status_code=400, detail="source_ids must contain 1-200 non-empty source identifiers")
        sources = {}
        for source_id in dict.fromkeys(source_ids):
            source = await active_index_store.get_source(source_id)
            if source is not None:
                sources[source_id] = source
        return {"ok": True, "sources": sources}

    @app.post("/api/v1/documents/chunks/query", tags=["documents"])
    async def document_chunks_query(payload: dict[str, Any]):
        source_ids = payload.get("source_ids")
        if (not isinstance(source_ids, list) or not source_ids
                or len(source_ids) > 200
                or any(not isinstance(value, str) or not value or len(value) > 2048
                       for value in source_ids)):
            raise HTTPException(status_code=400, detail="source_ids must contain 1-200 non-empty source identifiers")
        selected = set(source_ids)
        chunks = []
        for item in await document_store.all_chunks():
            document, chunk = item.document, item.chunk
            if document.source_id not in selected:
                continue
            chunks.append({
                "document_id": document.id,
                "source_id": document.source_id,
                "revision": document.revision,
                "title": document.title,
                "chunk_id": str(chunk.id),
                "chunk_ordinal": chunk.ordinal,
                "text": chunk.content,
                "chunk_profile": chunk.profile,
                "chunk_start": chunk.start,
                "chunk_end": chunk.end,
                "chunk_header": chunk.header,
                "session_id": document.metadata.get("session_id"),
                "project": document.metadata.get("project"),
                "role": document.metadata.get("role"),
                "kind": document.metadata.get("kind"),
                "is_subagent": document.metadata.get("is_subagent"),
                "text_sha": hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
            })
        return {"ok": True, "chunks": chunks}

    @app.get("/api/v1/vector/health", tags=["vector"])
    async def vector_health():
        try:
            await active_vector_store.list_collections()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="vector storage unavailable") from exc
        return {"ok": True, "service": "vector", "backend": "sqlite"}

    @app.get("/api/v1/vector/collections", tags=["vector"])
    async def vector_collections():
        return {"ok": True, "collections": await active_vector_store.list_collections()}

    @app.post("/api/v1/vector/collections/{name}/records/lookup", tags=["vector"])
    async def vector_existing(name: str, payload: dict[str, Any]):
        _validate_vector_collection_name(name)
        ids = payload.get("ids", [])
        if not isinstance(ids, list) or len(ids) > 1000 or any(
            not isinstance(item, str) or not item or len(item) > 512 for item in ids
        ):
            raise HTTPException(status_code=400, detail="ids must be a list of at most 1000 non-empty strings")
        return {"ok": True, "found": await active_vector_store.existing(name, ids)}

    @app.put("/api/v1/vector/collections/{name}/records", tags=["vector"])
    async def vector_upsert(name: str, payload: dict[str, Any]):
        _validate_vector_collection_name(name)
        ids, vectors = payload.get("ids"), payload.get("embeddings")
        documents_payload, metadatas = payload.get("documents"), payload.get("metadatas")
        if not isinstance(ids, list) or not isinstance(vectors, list) or not isinstance(documents_payload, list):
            raise HTTPException(status_code=400, detail="ids, embeddings, and documents must be lists")
        if not ids or len(ids) != len(vectors) or len(ids) != len(documents_payload):
            raise HTTPException(status_code=400, detail="ids, embeddings, and documents must be non-empty and equal length")
        if len(ids) > 256:
            raise HTTPException(status_code=413, detail="at most 256 records per upsert")
        if any(not isinstance(item, str) or not item or len(item) > 512 for item in ids):
            raise HTTPException(status_code=400, detail="ids must be non-empty strings of at most 512 characters")
        if len(set(ids)) != len(ids):
            raise HTTPException(status_code=400, detail="ids must be unique")
        status = embeddings.status()
        model = payload.get("model") or status.get("model")
        dimensions = payload.get("dimensions") or status.get("dimensions")
        if not isinstance(model, str) or not model or len(model) > 512:
            raise HTTPException(status_code=400, detail="model must be supplied or configured")
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or not 1 <= dimensions <= 8192:
            raise HTTPException(status_code=400, detail="dimensions must be an integer from 1 to 8192")
        if not isinstance(metadatas, list) or len(metadatas) != len(ids):
            raise HTTPException(status_code=400, detail="metadatas must match ids in length")
        items = []
        for record_id, vector, document, metadata in zip(ids, vectors, documents_payload, metadatas):
            if not isinstance(document, str) or not document:
                raise HTTPException(status_code=400, detail="documents must be non-empty strings")
            if not isinstance(metadata, dict) or not metadata:
                raise HTTPException(status_code=400, detail="each record needs a non-empty metadata object")
            if not isinstance(vector, list) or len(vector) != dimensions:
                raise HTTPException(status_code=400, detail=f"each embedding must have {dimensions} dimensions")
            try:
                vector = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="embedding values must be numeric") from exc
            import math
            if any(not math.isfinite(value) for value in vector):
                raise HTTPException(status_code=400, detail="embedding values must be finite")
            items.append({"id": record_id, "embedding": vector, "document": document, "metadata": metadata})
        try:
            await active_index_store.assert_generation_writes_open()
            count = await active_vector_store.upsert(name, model, dimensions, items)
            active_generation = await active_generation_id()
            if active_generation is not None:
                await active_vector_store.upsert_generation(
                    active_generation, name, model, dimensions, items,
                )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "upserted": count}

    @app.patch("/api/v1/vector/collections/{name}/records/metadata", tags=["vector"])
    async def vector_metadata(name: str, payload: dict[str, Any]):
        _validate_vector_collection_name(name)
        ids, metadatas = payload.get("ids"), payload.get("metadatas")
        if not isinstance(ids, list) or not isinstance(metadatas, list) or not ids or len(ids) != len(metadatas) or len(ids) > 256:
            raise HTTPException(status_code=400, detail="ids and metadatas must be non-empty lists of equal length, at most 256")
        if any(not isinstance(item, str) or not item for item in ids) or any(not isinstance(item, dict) for item in metadatas):
            raise HTTPException(status_code=400, detail="ids must be strings and metadatas must be objects")
        changes = [
            {"id": record_id, "metadata": metadata}
            for record_id, metadata in zip(ids, metadatas)
        ]
        try:
            await active_index_store.assert_generation_writes_open()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        updated = await active_vector_store.update_metadata(name, changes)
        active_generation = await active_generation_id()
        if active_generation is not None:
            await active_vector_store.update_generation_metadata(
                active_generation, name, changes,
            )
        return {"ok": True, "updated": updated}

    @app.post("/api/v1/vector/collections/{name}/records/prune", tags=["vector"])
    async def vector_delete_superseded(name: str, payload: dict[str, Any]):
        _validate_vector_collection_name(name)
        url, digest = payload.get("url"), payload.get("keep_sha256")
        if not isinstance(url, str) or not url or len(url) > 2048:
            raise HTTPException(status_code=400, detail="url must be a non-empty string of at most 2048 characters")
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise HTTPException(status_code=400, detail="keep_sha256 must be a lowercase hex SHA-256")
        try:
            await active_index_store.assert_generation_writes_open()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        deleted = await active_vector_store.delete_superseded(name, url, digest)
        active_generation = await active_generation_id()
        if active_generation is not None:
            remaining = await active_vector_store.list_records_for_source(name, url)
            await active_vector_store.delete_generation_source(
                active_generation, url, name=name,
            )
            if remaining:
                collection = next(
                    (item for item in await active_vector_store.list_collections()
                     if item["name"] == name), None,
                )
                if collection is not None:
                    await active_vector_store.upsert_generation(
                        active_generation, name, collection["model"],
                        collection["dimensions"], remaining,
                    )
        return {"ok": True, "deleted": deleted}

    @app.post("/api/v1/vector/collections/{name}/sources/tombstone", tags=["vector"])
    async def vector_source_tombstone(name: str, payload: dict[str, Any]):
        _validate_vector_collection_name(name)
        source_id = payload.get("source_id")
        if not isinstance(source_id, str) or not source_id or len(source_id) > 512:
            raise HTTPException(status_code=400, detail="source_id must be a non-empty string of at most 512 characters")
        try:
            await active_index_store.assert_generation_writes_open()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        deleted = await active_vector_store.delete_superseded(name, source_id, "")
        active_generation = await active_generation_id()
        if active_generation is not None:
            deleted += await active_vector_store.delete_generation_source(
                active_generation, source_id, name=name,
            )
        return {"ok": True, "deleted": deleted}

    @app.post("/api/v1/vector/collections/{name}/query", tags=["vector"])
    async def vector_query(name: str, payload: dict[str, Any]):
        _validate_vector_collection_name(name)
        vector, top_k = payload.get("embedding"), payload.get("top_k", 10)
        if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 200:
            raise HTTPException(status_code=400, detail="top_k must be an integer from 1 to 200")
        if not isinstance(vector, list):
            raise HTTPException(status_code=400, detail="embedding must be a list")
        status = embeddings.status()
        dimensions = payload.get("dimensions") or status.get("dimensions")
        if not isinstance(dimensions, int) or isinstance(dimensions, bool) or len(vector) != dimensions:
            # Existing collections define the authoritative dimension when no provider is configured.
            collection = next((item for item in await active_vector_store.list_collections() if item["name"] == name), None)
            dimensions = collection["dimensions"] if collection else dimensions
        if not isinstance(dimensions, int) or len(vector) != dimensions:
            raise HTTPException(status_code=400, detail="embedding dimension does not match collection")
        try:
            vector = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="embedding values must be numeric") from exc
        import math
        if any(not math.isfinite(value) for value in vector):
            raise HTTPException(status_code=400, detail="embedding values must be finite")
        requested_generation = payload.get("generation_id")
        if requested_generation is not None:
            active_generation = await active_generation_id()
            if requested_generation != active_generation:
                raise HTTPException(status_code=409, detail="generation_id is not the active verified generation")
            results = await active_vector_store.query_generation(
                requested_generation, name, vector, top_k,
            )
        else:
            results = await active_vector_store.query(name, vector, top_k)
        return {"ok": True, "results": results}

    @app.get("/api/v1/chunking/health", tags=["chunking"])
    async def chunking_health():
        return {"ok": True, "service": "chunking"}

    @app.get("/api/v1/chunking/ready", tags=["chunking"])
    async def chunking_ready():
        return {"ok": True, "ready": True, "profiles": sorted(CHUNKING_PROFILES)}

    @app.get("/api/v1/chunking/profiles", tags=["chunking"])
    async def chunking_profiles():
        return {"ok": True, "profiles": describe_chunk_profiles()}

    @app.post("/api/v1/chunking/chunk", tags=["chunking"])
    async def chunk_document(payload: dict[str, Any]):
        profile = payload.get("profile")
        try:
            chunks = chunk_text(
                profile,
                payload.get("source_id"),
                payload.get("text"),
                payload.get("media_type", "text/plain"),
            )
        except ChunkingError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        return {
            "ok": True,
            "profile": {"name": profile, "version": CHUNKING_PROFILES[profile]["version"]},
            "chunks": chunks,
        }

    @app.get("/api/v1/tokenization/health", tags=["tokenization"])
    async def tokenization_health():
        return {"ok": True, "service": "tokenization"}

    @app.get("/api/v1/tokenization/ready", tags=["tokenization"])
    async def tokenization_ready():
        return {"ok": True, "ready": True, "profiles": sorted(TOKENIZATION_PROFILES)}

    @app.get("/api/v1/tokenization/profiles", tags=["tokenization"])
    async def tokenization_profiles():
        return {"ok": True, "profiles": describe_profiles()}

    @app.post("/api/v1/tokenization/tokenize", tags=["tokenization"])
    async def tokenize_text(payload: dict[str, Any]):
        try:
            profile = payload.get("profile")
            tokens = tokenize(profile, payload.get("text"))
        except TokenizationError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        return {
            "ok": True,
            "profile": {"name": profile, "version": TOKENIZATION_PROFILES[profile]["version"]},
            "tokens": tokens,
        }

    @app.get("/api/v1/intent/health", tags=["intent"])
    async def intent_health():
        return {"ok": True, "service": "intent"}

    @app.get("/api/v1/intent/ready", tags=["intent"])
    async def intent_ready():
        return {"ok": True, "ready": True, "labels": sorted(INTENT_RULES)}

    @app.post("/api/v1/intent/classify", tags=["intent"])
    async def classify_intent(payload: dict[str, Any]):
        try:
            result = classify(payload.get("text"))
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.get("/api/v1/entities/health", tags=["entities"])
    async def entities_health():
        return {"ok": True, "service": "entities", "resolution": "graph-backed"}

    @app.get("/api/v1/entities/ready", tags=["entities"])
    async def entities_ready():
        try:
            await active_graph_store.get_node("__ensemble_entities_readiness_probe__")
        except Exception as exc:
            raise HTTPException(status_code=503, detail="entity identity store unavailable") from exc
        return {"ok": True, "ready": True, "identity_store": "graph"}

    @app.post("/api/v1/entities/extract", tags=["entities"])
    async def extract_entity_candidates(payload: dict[str, Any]):
        source = payload.get("source")
        if source is not None and (not isinstance(source, str) or len(source) > 512):
            raise HTTPException(status_code=400, detail="source must be a string of at most 512 characters")
        try:
            entities = extract(payload.get("text"), source=source)
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "entities": entities, "count": len(entities)}

    @app.post("/api/v1/entities/resolve", tags=["entities"])
    async def resolve_entity_candidates(payload: dict[str, Any]):
        candidates = payload.get("entities")
        if not isinstance(candidates, list) or not candidates or len(candidates) > 500:
            raise HTTPException(status_code=400, detail="entities must be a non-empty array of at most 500 candidates")
        resolved = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise HTTPException(status_code=400, detail="each entity candidate must be an object")
            kind, value = candidate.get("type"), candidate.get("normalized", candidate.get("text"))
            if not isinstance(kind, str) or not kind or len(kind) > 128 or not isinstance(value, str) or not value or len(value) > 2048:
                raise HTTPException(status_code=400, detail="each candidate requires a type and non-empty text or normalized value")
            normalized = normalize_entity(kind, value)
            matches = await active_graph_store.query_nodes("entity", {"entity_type": kind, "normalized": normalized})
            if len(matches) > 1:
                resolved.append({"status": "ambiguous", "candidate": candidate, "matches": matches})
                continue
            if matches:
                node, status = matches[0], "matched"
            else:
                identity = hashlib.sha256(f"{kind}:{normalized}".encode("utf-8")).hexdigest()
                properties = {
                    "entity_type": kind,
                    "normalized": normalized,
                    "canonical_name": normalized,
                }
                try:
                    node = await active_graph_store.add_node("entity", properties, node_id="entity_" + identity)
                except ValueError as exc:
                    raise HTTPException(status_code=409, detail="canonical entity identity conflicts with existing graph data") from exc
                status = "created"
            resolved.append({"status": status, "entity": node, "candidate_id": candidate.get("candidate_id")})
        return {"ok": True, "results": resolved, "count": len(resolved)}

    @app.post("/api/v1/entities/link", tags=["entities"])
    async def link_entities(payload: dict[str, Any]):
        source_id, target_id = payload.get("source_id"), payload.get("target_id")
        relationship = payload.get("relationship")
        properties = payload.get("properties", {})
        if not all(isinstance(value, str) and value for value in (source_id, target_id, relationship)):
            raise HTTPException(status_code=400, detail="source_id, target_id and relationship are required")
        if len(relationship) > 128 or not re.fullmatch(r"[a-z][a-z0-9_.-]*", relationship):
            raise HTTPException(status_code=400, detail="relationship must be a lowercase identifier")
        if not isinstance(properties, dict) or len(properties) > 64:
            raise HTTPException(status_code=400, detail="properties must be an object with at most 64 fields")
        source_node = await active_graph_store.get_node(source_id)
        target_node = await active_graph_store.get_node(target_id)
        if source_node is None or target_node is None:
            raise HTTPException(status_code=404, detail="one or both entity identities were not found")
        if source_node.get("type") != "entity" or target_node.get("type") != "entity":
            raise HTTPException(status_code=400, detail="entity links require two canonical entity nodes")
        try:
            edge = await active_graph_store.add_edge(source_id, target_id, relationship, properties)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "edge": edge}

    @app.get("/api/v1/reranking/health", tags=["reranking"])
    async def reranking_health():
        status = (
            ranking_provider.status() if ranking_provider is not None
            else {"ok": False, "ready": False, "provider": "disabled",
                  "error": "ranking provider is not configured"}
        )
        available = ranking_provider is not None and ranking_provider.available
        return JSONResponse(status_code=200 if available else 503, content=status)

    @app.get("/api/v1/reranking/ready", tags=["reranking"])
    async def reranking_ready():
        return await reranking_health()

    @app.post("/api/v1/reranking/rerank", tags=["reranking"])
    async def rerank_candidates(payload: dict[str, Any]):
        query, documents = payload.get("query"), payload.get("documents")
        try:
            validate_rerank_request(query, documents)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if ranking_provider is None or not ranking_provider.available:
            raise HTTPException(status_code=503, detail="ranking provider is not configured")
        try:
            scores = await ranking_provider.score(query, documents)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="ranking provider failed") from exc
        status = ranking_provider.status()
        return {"ok": True, "provider": status.get("provider"),
                "model": status.get("model"), "scores": scores}

    @app.get("/api/v1/ranking/health", tags=["ranking"])
    async def ranking_health():
        return {"ok": True, "service": "ranking"}

    @app.get("/api/v1/ranking/ready", tags=["ranking"])
    async def ranking_ready():
        return {"ok": True, "ready": True, "method": "reciprocal-rank-fusion"}

    @app.post("/api/v1/ranking/rank", tags=["ranking"])
    async def rank_candidates(payload: dict[str, Any]):
        try:
            results = fuse(
                payload.get("lists"),
                payload.get("top_k", 10),
                payload.get("k", DEFAULT_K),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "results": results}




    async def process_learning_outcome(payload: dict[str, Any]):
        request_id = payload.get("request_id") or str(uuid4())
        try:
            prepared = await active_learning_store.prepare_outcome(
                task_id=payload.get("task_id"), task_type=payload.get("task_type"),
                model=payload.get("model"), rating=payload.get("rating"),
                tokens=payload.get("tokens"), cost=payload.get("cost"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409 if "already exists" in str(exc) else 400, detail=str(exc)) from exc
        outcome = prepared["outcome"]
        if prepared["status"] == "duplicate":
            return {"ok": True, "duplicate": True, "checkpoint_advanced": True, "request_id": request_id}
        task_id = outcome["task_id"]
        try:
            memory_result = await active_memory_store.append(
                "learning-outcomes",
                {
                    "event_type": "learning.outcome", "source": "learning-service",
                    "task_id": task_id, "task_type": outcome["task_type"],
                    "model": outcome["model"], "rating": outcome["rating"],
                    "success": outcome["rating"] >= 3, "tokens": outcome["tokens"],
                    "cost": outcome["cost"], "timestamp": outcome["timestamp"],
                },
                event_id=task_id,
            )
            await active_thompson_store.record_outcome_once(
                event_id=task_id, model=outcome["model"],
                success=outcome["rating"] >= 3, cost=outcome["cost"], tokens=outcome["tokens"],
            )
            thompson_state = await active_thompson_store.get_state()
            await active_memory_store.append(
                "thompson-state",
                {"event_type": "thompson.state", "source": "learning-service", "task_id": task_id, "state": thompson_state},
                event_id="thompson-state-" + hashlib.sha256(task_id.encode("utf-8")).hexdigest(),
            )
            await active_learning_store.complete_outcome(task_id, True, True)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"message": "Learning outcome remains pending and can be retried", "checkpoint_advanced": False},
            ) from exc
        return {
            "ok": True, "memory": memory_result["status"] in {"stored", "duplicate"},
            "thompson": True, "thompson_state_memory": True,
            "checkpoint_advanced": True, "request_id": request_id,
        }

    async def record_learning_artifact(payload: dict[str, Any]):
        artifact = payload.get("artifact")
        try:
            artifact_status = await active_learning_store.record_artifact(artifact)
            artifact_type, run_id = artifact["artifact_type"], artifact["payload"]["run_id"]
            memory_result = await active_memory_store.append(
                "learning-artifacts",
                {"event_type": "learning.artifact", "source": "learning-service", "artifact": artifact},
                event_id="learning-artifact-" + hashlib.sha256(f"{artifact_type}:{run_id}".encode("utf-8")).hexdigest(),
            )
        except (KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="artifact must contain artifact_type and payload.run_id") from exc
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409 if "already exists" in str(exc) else 400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="artifact stored but Memory event acknowledgement failed") from exc
        return {
            "ok": True, "artifact_status": artifact_status,
            "memory": memory_result["status"] in {"stored", "duplicate"},
            "knowledge_promotion": "not_attempted",
        }

    @app.get("/api/v1/learning/health", tags=["learning"])
    async def learning_health():
        return {"ok": True, "service": "learning", "backend": "sqlite"}

    @app.get("/api/v1/learning/ready", tags=["learning"])
    async def learning_ready():
        try:
            await active_learning_store.recent_outcomes(1)
            await active_memory_store.check_ready()
            await active_thompson_store.get_state()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Learning dependency unavailable") from exc
        return {"ok": True, "ready": True, "dependencies": {"memory": True, "thompson": True}}

    @app.get("/api/v1/learning/ping", tags=["learning"])
    async def learning_ping():
        return {"ok": True, "message": "pong", "request_id": str(uuid4())}

    @app.post("/api/v1/learning/outcomes", tags=["learning"])
    async def learning_outcome(payload: dict[str, Any]):
        return await process_learning_outcome(payload)

    @app.get("/api/v1/learning/outcomes/{task_id}", tags=["learning"])
    async def learning_get_outcome(task_id: str):
        outcome = await active_learning_store.get_outcome(task_id)
        if outcome is None:
            raise HTTPException(status_code=404, detail="Learning outcome not found")
        return {"ok": True, "outcome": outcome}

    @app.get("/api/v1/learning/outcomes", tags=["learning"])
    async def learning_recent_outcomes(days: int = 7):
        try:
            outcomes = await active_learning_store.recent_outcomes(days)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "outcomes": outcomes}

    @app.get("/api/v1/learning/report", tags=["learning"])
    async def learning_report():
        return await active_learning_store.report()

    @app.post("/api/v1/learning/reset", tags=["learning"])
    async def learning_reset(payload: dict[str, Any]):
        if payload.get("confirm") is not True:
            raise HTTPException(status_code=400, detail="explicit confirm=true is required")
        await active_learning_store.reset()
        return {"ok": True, "reset": True, "thompson_state_preserved": True}

    @app.post("/api/v1/learning/artifacts", tags=["learning"])
    async def learning_artifact(payload: dict[str, Any]):
        return await record_learning_artifact(payload)

    @app.get("/api/v1/learning/artifacts", tags=["learning"])
    async def learning_artifacts(limit: int = 50):
        try:
            artifacts = await active_learning_store.list_artifacts(limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "artifacts": artifacts}

    @app.post("/api/v1/learning", tags=["learning"])
    async def learning_compatibility_route(payload: dict[str, Any]):
        operation = payload.get("op")
        if operation == "process_outcome":
            return await process_learning_outcome(payload)
        if operation == "record_artifact":
            return await record_learning_artifact(payload)
        if operation == "get_report":
            return await active_learning_store.report()
        if operation == "get_recent_outcomes":
            try:
                return {"ok": True, "outcomes": await active_learning_store.recent_outcomes(payload.get("days", 7))}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if operation == "reset_learning":
            if payload.get("confirm") is not True:
                raise HTTPException(status_code=400, detail="explicit confirm=true is required")
            await active_learning_store.reset()
            return {"ok": True}
        if operation == "ping":
            return {"ok": True, "message": "pong", "request_id": str(uuid4())}
        raise HTTPException(status_code=400, detail=f"unknown Learning operation: {operation}")

    async def thompson_select(payload: dict[str, Any]):
        task_type = payload.get("task_type", "unknown")
        required_capability = payload.get("required_capability", 0.5)
        max_cost = payload.get("max_cost", float("inf"))
        if "request_id" in payload and (not isinstance(payload["request_id"], str) or len(payload["request_id"]) > 256):
            raise HTTPException(status_code=400, detail="request_id must be a string of at most 256 characters")
        try:
            state = await active_thompson_store.get_state()
            model = thompson_select_model(state, task_type, required_capability, max_cost, thompson_rng)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "model": model, "request_id": payload.get("request_id") or str(uuid4())}

    async def thompson_record(payload: dict[str, Any]):
        task_type = payload.get("task_type", "unknown")
        if not isinstance(task_type, str):
            raise HTTPException(status_code=400, detail="task_type must be a string")
        try:
            event_id = payload.get("event_id")
            if event_id is not None:
                await active_thompson_store.record_outcome_once(
                    event_id, payload.get("model"), payload.get("success", False),
                    payload.get("cost", 0.0), payload.get("tokens", 0),
                )
            else:
                await active_thompson_store.record_outcome(
                    payload.get("model"), payload.get("success", False),
                    payload.get("cost", 0.0), payload.get("tokens", 0),
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "request_id": payload.get("request_id") or str(uuid4())}

    async def thompson_reset(payload: dict[str, Any]):
        try:
            ok = await active_thompson_store.reset(payload.get("model"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": ok, "request_id": payload.get("request_id") or str(uuid4())}

    @app.get("/api/v1/thompson/health", tags=["thompson"])
    async def thompson_health():
        return {"ok": True, "service": "thompson", "backend": "sqlite"}

    @app.get("/api/v1/thompson/ready", tags=["thompson"])
    async def thompson_ready():
        try:
            await active_thompson_store.get_state()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Thompson persistence unavailable") from exc
        return {"ok": True, "ready": True, "algorithm": "beta-thompson-sampling"}

    @app.get("/api/v1/thompson/ping", tags=["thompson"])
    async def thompson_ping():
        return {"ok": True, "message": "pong"}

    @app.get("/api/v1/thompson/state", tags=["thompson"])
    async def thompson_state():
        return {"ok": True, "state": await active_thompson_store.get_state()}

    @app.post("/api/v1/thompson/select", tags=["thompson"])
    async def thompson_select_route(payload: dict[str, Any]):
        return await thompson_select(payload)

    @app.post("/api/v1/thompson/outcomes", tags=["thompson"])
    async def thompson_outcome_route(payload: dict[str, Any]):
        return await thompson_record(payload)

    @app.post("/api/v1/thompson/reset", tags=["thompson"])
    async def thompson_reset_route(payload: dict[str, Any]):
        return await thompson_reset(payload)

    @app.post("/api/v1/thompson", tags=["thompson"])
    async def thompson_compatibility_route(payload: dict[str, Any]):
        action = payload.get("action")
        if action == "select_model":
            return await thompson_select(payload)
        if action == "record_outcome":
            return await thompson_record(payload)
        if action == "get_state":
            return {"ok": True, "state": await active_thompson_store.get_state(), "request_id": payload.get("request_id") or str(uuid4())}
        if action == "reset":
            return await thompson_reset(payload)
        if action == "ping":
            return {"ok": True, "message": "pong", "request_id": payload.get("request_id") or str(uuid4())}
        raise HTTPException(status_code=400, detail=f"unknown Thompson action: {action}")

    @app.get("/api/v1/ga/health", tags=["ga"])
    async def ga_health():
        return {"ok": True, "service": "ga", "systems": list(GA_SYSTEMS)}

    @app.get("/api/v1/ga/ready", tags=["ga"])
    async def ga_ready():
        try:
            await active_ga_store.list_runs(1)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="GA persistence unavailable") from exc
        return {
            "ok": True, "ready": True, "systems": list(GA_SYSTEMS),
            "parameter_schema_version": "1.0",
            "evaluation_scope": "synthetic-local",
        }

    @app.get("/api/v1/ga/optimizers", tags=["ga"])
    async def ga_optimizers():
        return {
            "ok": True,
            "parameter_schema_version": "1.0",
            "systems": {
                system: {
                    name: {"lower": spec["lower"], "upper": spec["upper"], "default": spec["default"]}
                    for name, spec in parameters.items()
                }
                for system, parameters in GA_PARAMETERS.items()
            },
            "evaluation_scope": "synthetic-local",
            "promotion_policy": "explicit-approval-required; approval records an export candidate and never changes live settings",
        }

    async def execute_ga_run(run_id: str, config: dict[str, Any]):
        from datetime import datetime, timezone
        await active_ga_store.update_run(
            run_id, status="running", started_at=datetime.now(timezone.utc)
        )
        try:
            result = await asyncio.to_thread(
                GeneticAlgorithm(GAConfig(**config)).run
            )
            await active_ga_store.update_run(
                run_id, status="completed", result=result,
                completed_at=datetime.now(timezone.utc), error=None,
            )
        except Exception as exc:
            await active_ga_store.update_run(
                run_id, status="failed", error=f"{type(exc).__name__}: {exc}",
                completed_at=datetime.now(timezone.utc),
            )

    @app.post("/api/v1/ga/runs", status_code=202, tags=["ga"])
    async def ga_create_run(payload: dict[str, Any], background_tasks: BackgroundTasks):
        allowed = {"population_size", "generations", "crossover_rate", "mutation_rate", "tournament_k", "seed"}
        unknown = set(payload) - allowed
        if unknown:
            raise HTTPException(status_code=400, detail=f"unsupported GA config fields: {', '.join(sorted(unknown))}")
        config = {
            "population_size": payload.get("population_size", 50),
            "generations": payload.get("generations", 25),
            "crossover_rate": payload.get("crossover_rate", 0.8),
            "mutation_rate": payload.get("mutation_rate", 0.1),
            "tournament_k": payload.get("tournament_k", 3),
            "seed": payload.get("seed") if payload.get("seed") is not None else secrets.randbits(32),
        }
        try:
            GAConfig(**config)
            GeneticAlgorithm(GAConfig(**config))._validate_config()
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        run = await active_ga_store.create_run(config)
        background_tasks.add_task(execute_ga_run, run["id"], config)
        return {"ok": True, "run": run}

    @app.get("/api/v1/ga/runs", tags=["ga"])
    async def ga_list_runs(limit: int = 50):
        try:
            runs = await active_ga_store.list_runs(limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "runs": runs}

    @app.get("/api/v1/ga/runs/{run_id}", tags=["ga"])
    async def ga_get_run(run_id: str):
        run = await active_ga_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="GA run not found")
        return {"ok": True, "run": run}

    @app.post("/api/v1/ga/runs/{run_id}/promote", tags=["ga"])
    async def ga_promote_candidate(run_id: str, payload: dict[str, Any]):
        if payload.get("approved") is not True:
            raise HTTPException(status_code=400, detail="explicit approved=true is required")
        system = payload.get("system")
        rank = payload.get("rank", 1)
        reason = payload.get("reason")
        approved_by = payload.get("approved_by")
        if system not in GA_SYSTEMS:
            raise HTTPException(status_code=400, detail="unknown GA system")
        if isinstance(rank, bool) or not isinstance(rank, int) or not 1 <= rank <= 3:
            raise HTTPException(status_code=400, detail="rank must be between 1 and 3")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2048:
            raise HTTPException(status_code=400, detail="reason is required and limited to 2048 characters")
        if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 256:
            raise HTTPException(status_code=400, detail="approved_by is required and limited to 256 characters")
        run = await active_ga_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="GA run not found")
        if run["status"] != "completed" or not run.get("result"):
            raise HTTPException(status_code=409, detail="only completed GA runs can be promoted")
        candidates = run["result"].get("best_by_system", {}).get(system, [])
        candidate = next((item for item in candidates if item.get("rank") == rank), None)
        if candidate is None:
            raise HTTPException(status_code=404, detail="GA candidate not found")
        try:
            promotion = await active_ga_store.promote(run_id, system, candidate, reason.strip(), approved_by.strip())
        except Exception as exc:
            raise HTTPException(status_code=409, detail="candidate already has a promotion record or persistence failed") from exc
        return {
            "ok": True, "promotion": promotion,
            "applied": False,
            "message": "Candidate approved for export only; no live configuration was changed.",
        }


    @app.post("/api/v1/memory/search/semantic", tags=["memory"])
    async def memory_search_semantic(payload: dict[str, Any]):
        try:
            top_k = payload.get("top_k", payload.get("limit", 10))
            result = await active_memory_store.search_semantic(
                payload.get("query", ""), top_k, payload.get("source")
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "results": result}

    @app.post("/api/v1/memory/search/hybrid", tags=["memory"])
    async def memory_search_hybrid(payload: dict[str, Any]):
        try:
            top_k = payload.get("top_k", payload.get("limit", 10))
            result = await active_memory_store.search_hybrid(payload.get("query", ""), top_k)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "results": result}

    @app.post("/api/v1/memory/search/keywords", tags=["memory"])
    async def memory_search_keywords(payload: dict[str, Any]):
        try:
            result = await active_memory_store.search(payload.get("keywords", []), payload.get("limit", 50))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "results": result}

    @app.post("/api/v1/memory/sources/import", tags=["memory"])
    async def memory_source_import(payload: dict[str, Any]):
        try:
            result = await active_memory_store.ingest_claude_markdown(
                payload.get("path", ""), payload.get("content", ""),
                payload.get("sha256", ""), payload.get("metadata", {}),
            )
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.post("/api/v1/memory/sources/reconcile", tags=["memory"])
    async def memory_sources_reconcile(payload: dict[str, Any]):
        try:
            result = await active_memory_store.reconcile_claude_markdown(
                payload.get("source", ""), payload.get("scope", ""), payload.get("paths", [])
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.get("/api/v1/memory/sources/status", tags=["memory"])
    async def memory_sources_status():
        return {"ok": True, **(await active_memory_store.knowledge_status())}

    @app.get("/api/v1/context/health", tags=["context"])
    async def context_health():
        return {"ok": True, "service": "context", "assembly": "extractive"}

    @app.get("/api/v1/context/ready", tags=["context"])
    async def context_ready():
        try:
            await document_store.check_ready()
            await active_memory_store.check_ready()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="context dependencies unavailable") from exc
        return {"ok": True, "ready": True, "token_estimator": "characters-divided-by-four"}

    @app.post("/api/v1/context/assemble", tags=["context"])
    async def context_assemble(payload: dict[str, Any], request: Request):
        query = payload.get("query")
        limit = payload.get("limit", 10)
        max_tokens = payload.get("max_tokens", 4000)
        include_memory = payload.get("include_memory", True)
        include_search = payload.get("include_search", True)
        memory_name = payload.get("memory_name")
        execution_context = payload.get("execution_context")
        if not isinstance(include_memory, bool) or not isinstance(include_search, bool):
            raise HTTPException(status_code=400, detail="include_memory and include_search must be booleans")
        if memory_name is not None and (
            not isinstance(memory_name, str) or not isinstance(execution_context, dict)
        ):
            raise HTTPException(status_code=400, detail="memory_name requires an execution_context object")
        if execution_context is not None and memory_name is None:
            raise HTTPException(status_code=400, detail="execution_context requires memory_name")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 32_000:
            raise HTTPException(status_code=400, detail="max_tokens must be an integer between 1 and 32000")
        if not isinstance(query, str) or not query.strip() or len(query) > 20_000:
            raise HTTPException(status_code=400, detail="query must be a non-empty string of at most 20000 characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise HTTPException(status_code=400, detail="limit must be an integer between 1 and 100")

        internal = InternalRestClient(app, {
            "search": os.getenv("ENSEMBLE_SEARCH_REST_URL"),
            "memory": os.getenv("ENSEMBLE_MEMORY_REST_URL"),
            "intent": os.getenv("ENSEMBLE_INTENT_REST_URL"),
            "entities": os.getenv("ENSEMBLE_ENTITIES_REST_URL"),
        })
        candidates = []
        degraded = []
        intent_result = None
        entity_result = []
        if include_search:
            try:
                found = await internal.request(
                    "POST", "/api/v1/search/hybrid",
                    request=request,
                    json={"query": query, "limit": limit, "rerank": False},
                )
                degraded.extend(found.get("degraded", []))
                graph = found.get("graph_expansion", {})
                if graph.get("degraded"):
                    degraded.append("graph")
                for hit in found.get("results", []):
                    candidates.append({
                        "source": "ensemble-retrieval",
                        "title": hit.get("title", ""),
                        "content": hit.get("text", ""),
                        "score": hit.get("fusion_score", 0.0),
                        "source_id": hit.get("source_id"),
                        "revision": hit.get("revision"),
                        "chunk_id": hit.get("chunk_id"),
                        "graph_score": hit.get("graph_score", 0.0),
                    })
            except Exception:
                degraded.append("search")

        if include_memory:
            try:
                if memory_name is not None:
                    recalled = await internal.request(
                        "POST", "/api/v1/memory/recall",
                        request=request,
                        json={
                            "name": memory_name,
                            "context": execution_context,
                            "limit": limit,
                        },
                    )
                    priorities = {
                        "parent": 100, "ancestor": 90,
                        "same-request": 70, "related-lineage": 60,
                    }
                    for item in recalled.get("results", []):
                        candidates.append({
                            "source": "memory-recall",
                            "title": item.get("relation", "related execution"),
                            "content": json.dumps(
                                item.get("record", {}), ensure_ascii=False, sort_keys=True
                            ),
                            "score": priorities.get(item.get("relation"), 0),
                            "relation": item.get("relation"),
                            "execution_context": item.get("execution_context"),
                        })

                semantic = await internal.request(
                    "POST", "/api/v1/memory/search/semantic",
                    request=request,
                    json={"query": query, "top_k": limit},
                )
                for item in semantic.get("results", []):
                    file_name, section = item.get("file"), item.get("section", "full")
                    content = item.get("content", "")
                    if isinstance(file_name, str):
                        chunked = await internal.request(
                            "POST", "/api/v1/memory/chunking",
                            request=request,
                            json={"name": file_name, "size": 2000},
                        )
                        selected = next(
                            (part for part in chunked.get("chunks", [])
                             if part.get("header") == section),
                            None,
                        )
                        if selected:
                            content = selected.get("content", content)
                    candidates.append({
                        "source": "memory-document",
                        "title": f"{file_name} · {section}",
                        "content": content,
                        "score": float(item.get("score", 0.0)),
                        "file": file_name,
                        "section": section,
                    })
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400:
                    raise HTTPException(status_code=400, detail="memory request was invalid") from exc
                degraded.append("memory")
            except Exception:
                degraded.append("memory")

        try:
            intent_result = await internal.request(
                "POST", "/api/v1/intent/classify",
                request=request, json={"text": query},
            )
        except Exception:
            degraded.append("intent")

        try:
            entity_result = (await internal.request(
                "POST", "/api/v1/entities/extract",
                request=request, json={"text": query, "source": "context.query"},
            )).get("entities", [])
        except Exception:
            degraded.append("entities")

        candidates.sort(key=lambda item: -float(item.get("score", 0.0)))
        try:
            assembled = _assemble_context(candidates, query, max_tokens)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True, **assembled, "query": query,
            "intent": intent_result, "entities": entity_result,
            "degraded": sorted(set(degraded)),
        }

    @app.get("/api/v1/memory/health", tags=["memory"])
    async def memory_health():
        return {"ok": True, "service": "memory", "backend": "sqlite"}

    @app.get("/api/v1/memory/ready", tags=["memory"])
    async def memory_ready():
        try:
            await active_memory_store.check_ready()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="memory persistence unavailable") from exc
        return {"ok": True, "ready": True, "backend": "sqlite"}

    @app.get("/api/v1/memory/memories", tags=["memory"])
    async def memory_list():
        return {"ok": True, "memories": await active_memory_store.list_names()}

    @app.get("/api/v1/memory/memories/{name}", tags=["memory"])
    async def memory_read(name: str):
        try:
            content = await active_memory_store.read(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if content is None:
            raise HTTPException(status_code=404, detail="memory not found")
        return {"ok": True, "content": content}

    @app.put("/api/v1/memory/memories/{name}", tags=["memory"])
    async def memory_write(name: str, payload: dict[str, Any]):
        try:
            await active_memory_store.write(name, payload.get("content"))
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.delete("/api/v1/memory/memories/{name}", tags=["memory"])
    async def memory_delete(name: str):
        try:
            result = await active_memory_store.delete(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result["status"] == "not_found":
            raise HTTPException(status_code=404, detail="memory not found")
        return {"ok": True, **result}

    @app.post("/api/v1/memory/memories/{name}/entries", tags=["memory"])
    async def memory_append(name: str, payload: dict[str, Any]):
        try:
            result = await active_memory_store.append(name, payload.get("entry", {}))
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.put("/api/v1/memory/memories/{name}/entries/{event_id}", tags=["memory"])
    async def memory_append_once(name: str, event_id: str, payload: dict[str, Any]):
        try:
            result = await active_memory_store.append(
                name, payload.get("entry", {}), event_id
            )
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409 if "already exists" in str(exc) else 400, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.get("/api/v1/memory/memories/{name}/entries", tags=["memory"])
    async def memory_entries(name: str, limit: int = 1000):
        try:
            result = await active_memory_store.read_entries(name, limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "entries": result}

    @app.post("/api/v1/memory/recall", tags=["memory"])
    async def memory_recall(payload: dict[str, Any]):
        try:
            result = await active_memory_store.retrieve(
                payload.get("name"), payload.get("context", {}), payload.get("limit", 10)
            )
            response = {"ok": True, "results": result}
            if "max_tokens" in payload:
                candidates = []
                for item in result:
                    record = item.get("record", {})
                    if not isinstance(record, dict):
                        continue
                    parts = []
                    for key in ("content", "text", "summary", "prompt", "response", "output"):
                        value = record.get(key)
                        if isinstance(value, str) and value.strip():
                            parts.append(f"{key}: {value.strip()}")
                    if not parts:
                        continue
                    execution = item.get("execution_context", {})
                    candidates.append({
                        "source": "memory:" + str(payload.get("name")),
                        "title": str(record.get("title") or record.get("event_id") or "memory entry"),
                        "content": "\n".join(parts),
                        "event_id": record.get("event_id"),
                        "relation": item.get("relation"),
                        "execution_context": execution,
                    })
                response["context"] = _assemble_context(
                    candidates, payload.get("context", {}).get("objective", ""),
                    payload["max_tokens"],
                )
            return response
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/memory/chunking", tags=["memory"])
    async def memory_chunk(payload: dict[str, Any]):
        try:
            result = await active_memory_store.chunk(payload.get("name"), payload.get("size", 2000))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "chunks": result}

    @app.post("/api/v1/memory/events", tags=["memory"])
    async def memory_capture_prompt(payload: dict[str, Any], request: Request):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 262_144:
            raise HTTPException(status_code=413, detail="request body exceeds 256 KiB")
        session_id, prompt = payload.get("session_id"), payload.get("prompt")
        timestamp, cwd = payload.get("timestamp"), payload.get("cwd")
        if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
            raise HTTPException(status_code=400, detail="session_id must be a non-empty string of at most 256 characters")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 65_536:
            raise HTTPException(status_code=400, detail="prompt must be a non-empty string of at most 65536 characters")
        if not isinstance(timestamp, str) or not timestamp or len(timestamp) > 128:
            raise HTTPException(status_code=400, detail="timestamp must be a non-empty string of at most 128 characters")
        if not isinstance(cwd, str) or len(cwd) > 4096:
            raise HTTPException(status_code=400, detail="cwd must be a string of at most 4096 characters")
        event_id = hashlib.sha256(f"{session_id}|{timestamp}|{prompt}".encode("utf-8")).hexdigest()
        try:
            stored = await active_memory_store.append(
                "prompt-ledger",
                {"session_id": session_id, "timestamp": timestamp, "cwd": cwd, "prompt": prompt},
                event_id=event_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "ok": True, "event_id": event_id, "status": stored["status"],
            "vector_indexed": False, "lexical_indexed": False,
            "index_error": None,
        }

    @app.get("/api/v1/graph/health", tags=["graph"])
    async def graph_health():
        return {"ok": True, "service": "graph", "backend": "sqlite"}

    @app.post("/api/v1/graph/nodes", tags=["graph"])
    async def graph_add_node(payload: dict[str, Any]):
        try:
            node = await active_graph_store.add_node(payload.get("type"), payload.get("properties", {}), payload.get("id"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "node": node}

    @app.post("/api/v1/graph/edges", tags=["graph"])
    async def graph_add_edge(payload: dict[str, Any]):
        try:
            edge = await active_graph_store.add_edge(payload.get("source"), payload.get("target"), payload.get("type"), payload.get("properties", {}), payload.get("id"))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "edge": edge}

    @app.get("/api/v1/graph/nodes/{node_id}", tags=["graph"])
    async def graph_get_node(node_id: str):
        node = await active_graph_store.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="node not found")
        return {"ok": True, "node": node}

    @app.post("/api/v1/graph/query", tags=["graph"])
    async def graph_query(payload: dict[str, Any]):
        try:
            if payload.get("type") == "edges":
                result = await active_graph_store.query_edges(payload.get("from_type"), payload.get("to_type"), payload.get("relationship"))
                return {"ok": True, "edges": result}
            result = await active_graph_store.query_nodes(payload.get("type"), payload.get("properties", {}))
            return {"ok": True, "nodes": result}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/graph/traverse", tags=["graph"])
    async def graph_traverse(payload: dict[str, Any]):
        try:
            result = await active_graph_store.traverse(payload.get("start"), payload.get("direction", "out"), payload.get("max_depth", 1), payload.get("edge_type"))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "results": result}

    @app.get("/control", response_class=HTMLResponse, include_in_schema=False)
    async def control_panel():
        from pathlib import Path
        return HTMLResponse(Path(__file__).with_name("control_panel.html").read_text(encoding="utf-8"))

    @app.get("/control.js", include_in_schema=False)
    async def control_script():
        from pathlib import Path
        from fastapi.responses import Response
        return Response(Path(__file__).with_name("control.js").read_text(encoding="utf-8"), media_type="application/javascript")

    @app.post("/api/v1/ingest/files", status_code=201, tags=["ingestion"])
    async def ingest_pdf(request: Request, source_id: str, title: str, revision: int = 1):
        """Extract text from a bounded PDF upload and pass it through document ingestion."""
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/pdf":
            raise HTTPException(status_code=415, detail="Content-Type must be application/pdf")
        if not source_id.strip() or len(source_id) > 1024:
            raise HTTPException(status_code=400, detail="source_id must be 1-1024 characters")
        if not title.strip() or len(title) > 2048:
            raise HTTPException(status_code=400, detail="title must be 1-2048 characters")
        if revision < 1:
            raise HTTPException(status_code=400, detail="revision must be positive")
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_PDF_BYTES:
            raise HTTPException(status_code=413, detail="PDF exceeds the 20 MiB upload limit")
        raw = await request.body()
        try:
            extracted = extract_pdf(raw)
            document = DocumentIn(
                source_id=source_id, revision=revision, title=title,
                content=extracted["text"], media_type="application/pdf",
                metadata={
                    "source_type": "pdf",
                    "page_count": extracted["page_count"],
                    "extracted_pages": extracted["extracted_pages"],
                },
            )
            result, decision = await ingest_indexed_document(document, origin="pdf", locator=source_id, request=request)
            return {"ok": True, "decision": decision, "document": result, "extraction": {
                "page_count": extracted["page_count"],
                "extracted_pages": extracted["extracted_pages"],
            }}
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except PDFExtractionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except HTTPException:
            raise
        except ChunkingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="PDF ingestion failed") from exc

    @app.post("/api/v1/ingest/urls/preview", tags=["ingestion"])
    async def scrape_preview(payload: dict[str, Any]):
        try:
            return await preview_uri(payload.get("uri"))
        except ScrapeError as exc:
            raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc

    @app.post("/api/v1/ingest/urls", status_code=201, tags=["ingestion"])
    async def scrape_and_ingest(payload: dict[str, Any], request: Request):
        try:
            preview = await preview_uri(payload.get("uri"))
            source_id = preview["final_uri"]
            current = await active_index_store.get_source(source_id)
            revision = payload.get("revision")
            if revision is None:
                revision = (current["high_sequence"] + 1) if current else 1
            digest = hashlib.sha256(preview["text"].encode("utf-8")).hexdigest()
            profiles = {"chunking": "window-v1", "media_type": "text/plain"}
            document = DocumentIn(
                source_id=source_id, revision=revision, title=preview["title"],
                content=preview["text"], chunk_profile="window-v1",
                metadata={"origin": "web", "locator": source_id},
            )
            result, decision = await ingest_indexed_document(
                document, origin="web", locator=source_id, request=request
            )
            await active_knowledge_store.put({
                "source_id": source_id, "revision_sequence": revision, "revision_id": digest,
                "title": preview["title"], "content": preview["text"], "origin": "web",
                "locator": source_id, "profiles": profiles,
            })
            return {"ok": True, "decision": decision, "source": preview, "document": result}
        except ScrapeError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="scrape ingestion failed") from exc

    @app.get("/api/v1/search/lexical/health", tags=["search"])
    async def lexical_health():
        return {"ok": True, "service": "lexical", "backend": "document-store"}

    @app.get("/api/v1/search/lexical/ready", tags=["search"])
    async def lexical_ready():
        try:
            await document_store.check_ready()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="lexical document store unavailable") from exc
        return {"ok": True, "ready": True}

    @app.post("/api/v1/lexical/documents", status_code=201, tags=["search"])
    async def lexical_ingest_document(payload: dict[str, Any], request: Request, response: Response):
        # The POC contract accepts a session plus messages. The document form is
        # retained as a convenience for callers that already have a document.
        if "session" in payload or "messages" in payload:
            response.status_code = 200
            return await ingest_session(payload, request)
        try:
            document_request = DocumentIn.model_validate(payload)
            result, _ = await ingest_indexed_document(document_request, embed=False, request=request)
            return result
        except ChunkingError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="lexical document ingestion failed") from exc

    @app.post("/api/v1/search/lexical", tags=["search"])
    async def lexical_search(request: SearchIn):
        try:
            results = await search.lexical_search(request.query, request.limit)
            return {"ok": True, "results": results, "retrieval": "lexical"}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/search/hybrid/health", tags=["search"])
    async def hybrid_health():
        return {"ok": True, "service": "hybrid"}

    @app.get("/api/v1/search/hybrid/ready", tags=["search"])
    async def hybrid_ready():
        try:
            await document_store.check_ready()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="hybrid retrieval persistence unavailable") from exc
        embedding_available = bool(embeddings.available)
        return {
            "ok": True, "ready": True,
            "status": "ok" if embedding_available else "partial",
            "channels": {
                "lexical": {"status": "ready", "available": True},
                "vector": {"status": "ready" if embedding_available else "unavailable",
                           "available": embedding_available},
            },
            "embedding_available": embedding_available,
            "degraded_mode": None if embedding_available else "lexical fallback",
        }

    @app.post("/api/v1/search/retrieve", tags=["search"])
    async def search_retrieve(search_request: SearchIn):
        """Execute the Search capability's lexical/vector retrieval contract."""
        try:
            return await search.search_with_status(
                search_request.query, search_request.limit, rerank=search_request.rerank
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="search retrieval failed") from exc

    @app.post("/api/v1/search/hybrid", tags=["search"])
    async def hybrid_search(search_request: SearchIn, request: Request):
        internal = InternalRestClient(app, {
            "graph": os.getenv("ENSEMBLE_GRAPH_REST_URL"),
            "entities": os.getenv("ENSEMBLE_ENTITIES_REST_URL"),
            "intent": os.getenv("ENSEMBLE_INTENT_REST_URL"),
            "reranking": os.getenv("ENSEMBLE_RERANKING_REST_URL"),
            "memory": os.getenv("ENSEMBLE_MEMORY_REST_URL"),
            "search": os.getenv("ENSEMBLE_SEARCH_REST_URL"),
        })
        try:
            # All capability composition crosses the versioned REST contracts.
            # Search owns its retrieval algorithm and persistence implementation.
            result = await internal.request(
                "POST", "/api/v1/search/retrieve", request=request,
                json=search_request.model_dump(mode="json"),
            )
            # HTTP JSON decoding returns dictionaries; restore the typed
            # SearchHit contract before Hybrid applies graph/rerank/ledger logic.
            result["results"] = [
                SearchHit.model_validate(hit) for hit in result.get("results", [])
            ]

            try:
                intent = await internal.request(
                    "POST", "/api/v1/intent/classify", request=request,
                    json={"text": search_request.query},
                )
            except Exception:
                intent = None
                result["degraded"].append("intent")

            try:
                entities_response = await internal.request(
                    "POST", "/api/v1/entities/extract", request=request,
                    json={"text": search_request.query, "source": "search.query"},
                )
                entities = entities_response.get("entities", [])
            except Exception:
                entities = []
                result["degraded"].append("entities")

            result["intent"] = intent
            result["entities"] = entities
            try:
                graph_timeout = float(os.getenv("ENSEMBLE_RETRIEVAL_GRAPH_TIMEOUT", "2"))
            except (TypeError, ValueError):
                graph_timeout = 2.0
            if not math.isfinite(graph_timeout):
                graph_timeout = 2.0
            graph_timeout = min(30.0, max(0.05, graph_timeout))
            graph_started = time.perf_counter()
            try:
                result["results"], graph_expansion = await asyncio.wait_for(
                    expand_graph_results(
                        search_request.query, entities, result["results"],
                        search_request.limit, internal, request,
                    ),
                    timeout=graph_timeout,
                )
            except TimeoutError:
                graph_expansion = {
                    "matched_entities": [], "candidate_sources": 0,
                    "degraded": True, "timed_out": True,
                }
            graph_expansion["duration_ms"] = round(
                (time.perf_counter() - graph_started) * 1000, 3
            )
            result["graph_expansion"] = graph_expansion
            if graph_expansion.get("timed_out"):
                result["degraded"].append("graph_timeout")
            elif graph_expansion.get("degraded"):
                result["degraded"].append("graph")
            elif graph_expansion.get("candidate_sources"):
                result["retrieval"] = "hybrid"

            rerank_enabled = (
                search_request.rerank if search_request.rerank is not None
                else os.getenv("ENSEMBLE_RERANK_ENABLED", "0").strip() == "1"
            )
            if rerank_enabled and result["results"]:
                candidates = result["results"][:min(30, len(result["results"]))]
                try:
                    reranked = await internal.request(
                        "POST", "/api/v1/reranking/rerank", request=request,
                        json={
                            "query": search_request.query,
                            "documents": [candidate.text[:4000] for candidate in candidates],
                        },
                    )
                    scores = reranked.get("scores", [])
                    if len(scores) != len(candidates) or any(
                        not math.isfinite(float(score)) for score in scores
                    ):
                        raise RuntimeError("reranker returned invalid or misaligned scores")
                    reranked_hits = [
                        hit.model_copy(update={"rerank_score": float(score)})
                        for hit, score in zip(candidates, scores)
                    ]
                    result["results"] = sorted(
                        reranked_hits, key=lambda hit: hit.rerank_score, reverse=True
                    ) + result["results"][len(candidates):]
                    result["reranked"] = True
                except Exception:
                    result["degraded"].append("reranking")

            related = []
            try:
                exact_paths = {
                    item["text"] for item in entities
                    if item.get("exact") and item.get("type") in {"path", "file"}
                }
                if exact_paths:
                    file_nodes = (await internal.request(
                        "POST", "/api/v1/graph/query", request=request,
                        json={"type": "file", "properties": {}},
                    )).get("nodes", [])
                    sessions = {}
                    for file_node in file_nodes:
                        path = (file_node.get("properties") or {}).get("path", "")
                        if not isinstance(path, str) or not any(
                            path == candidate or path.endswith("/" + candidate)
                            for candidate in exact_paths
                        ):
                            continue
                        touched = (await internal.request(
                            "POST", "/api/v1/graph/traverse", request=request,
                            json={"start": file_node["id"], "direction": "in",
                                  "max_depth": 1, "edge_type": "touched"},
                        )).get("results", [])
                        for hit in touched:
                            node = hit["node"]
                            props = node.get("properties") or {}
                            session_id = props.get("session_id")
                            if session_id:
                                entry = sessions.setdefault(session_id, {
                                    "session_id": session_id,
                                    "project": props.get("project"),
                                    "touched": [],
                                })
                                entry["touched"].append(path)
                    related = list(sessions.values())[:10]
            except Exception:
                result["degraded"].append("graph")
            result["related_sessions"] = related
            if graph_expansion.get("candidate_sources"):
                result["ok"] = True
                if result.get("status") == "unavailable":
                    result["status"] = "partial"
            if result.get("degraded") and result.get("status") == "ok":
                result["status"] = "partial"
            result["degraded"] = sorted(set(result.get("degraded", [])))

            # Successful searches are recorded for audit/learning through the
            # Memory resource contract. Ledger failure never fails retrieval.
            try:
                await internal.request(
                    "PUT",
                    f"/api/v1/memory/memories/search-ledger/entries/search-{uuid4()}",
                    request=request,
                    json={"entry": {
                        "query": search_request.query,
                        "intent": result["intent"],
                        "degraded": result["degraded"],
                        "result_count": len(result["results"]),
                        "results": [{
                            "source_id": hit.source_id,
                            "revision": hit.revision,
                            "chunk_id": str(hit.chunk_id),
                            "score": hit.fusion_score,
                        } for hit in result["results"]],
                    }},
                )
            except Exception:
                pass
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="hybrid search failed") from exc

    @app.post("/api/v1/search/hybrid/knowledge", tags=["search"])
    async def hybrid_knowledge_search(search_request: SearchIn, request: Request):
        internal = InternalRestClient(app, {
            "embeddings": os.getenv("ENSEMBLE_EMBEDDINGS_REST_URL"),
            "vector": os.getenv("ENSEMBLE_VECTOR_REST_URL"),
            "index": os.getenv("ENSEMBLE_INDEX_REST_URL"),
        })
        try:
            embedded = await internal.request(
                "POST", "/api/v1/embeddings/embed", request=request,
                json={"texts": [search_request.query], "mode": "query"},
            )
            vectors = embedded.get("embeddings", [])
            if len(vectors) != 1:
                raise RuntimeError("embedding capability returned an invalid result")
            response = await internal.request(
                "POST", "/api/v1/vector/collections/knowledge_chunks/query",
                request=request,
                json={
                    "embedding": vectors[0],
                    "top_k": min(200, max(search_request.limit, search_request.limit * 4)),
                    "dimensions": embedded.get("dimensions"),
                    "generation_id": await active_generation_id(),
                },
            )
            candidates = response.get("results", [])
            source_ids = sorted({
                item.get("metadata", {}).get("source_id")
                for item in candidates
                if isinstance(item.get("metadata"), dict)
                and isinstance(item["metadata"].get("source_id"), str)
            })
            states = (await internal.request(
                "POST", "/api/v1/index/sources/query", request=request,
                json={"source_ids": source_ids},
            )).get("sources", {})
            results = []
            for candidate in candidates:
                metadata = candidate.get("metadata")
                if not isinstance(metadata, dict):
                    continue
                source_id, revision = metadata.get("source_id"), metadata.get("revision")
                if (
                    not isinstance(source_id, str) or not source_id
                    or isinstance(revision, bool) or not isinstance(revision, int)
                ):
                    continue
                source = states.get(source_id)
                if (
                    source is None or source.get("tombstoned")
                    or source.get("high_sequence") != revision
                ):
                    continue
                results.append(candidate)
                if len(results) >= search_request.limit:
                    break
            return {
                "ok": True, "results": results, "degraded": [],
                "retrieval": "knowledge-only",
            }
        except Exception as exc:
            raise HTTPException(status_code=503, detail="knowledge retrieval dependency unavailable") from exc

    @app.post("/api/v1/ingest/transcripts", tags=["ingestion"])
    async def hybrid_ingest_transcript(payload: dict[str, Any], request: Request):
        """Ingest a caller-supplied JSONL slice with a durable, revision-scoped checkpoint."""
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_TRANSCRIPT_BYTES + 65_536:
            raise HTTPException(status_code=413, detail="request body exceeds transcript limit")
        raw_content = payload.get("content")
        session_id = payload.get("session_id")
        project = payload.get("project")
        file_path = payload.get("file", "transcript.jsonl")
        offset = payload.get("offset", 0)
        revision_id = payload.get("revision_id", "1")
        if not isinstance(raw_content, str):
            raise HTTPException(status_code=400, detail="content must be a string")
        if not isinstance(project, str) or not project.strip() or len(project) > 256:
            raise HTTPException(status_code=400, detail="project must be a non-empty string of at most 256 characters")
        if not isinstance(file_path, str) or not file_path.strip() or len(file_path) > 4096:
            raise HTTPException(status_code=400, detail="file must be a non-empty logical source label of at most 4096 characters")
        if not isinstance(revision_id, str) or not revision_id.strip() or len(revision_id) > 256:
            raise HTTPException(status_code=400, detail="revision_id must be a non-empty string of at most 256 characters")
        source_key = hashlib.sha256(f"{project}|{file_path}".encode("utf-8")).hexdigest()
        source_id = "transcript-file:" + source_key
        payload_digest = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
        job_id = hashlib.sha256(
            f"{source_id}\\0{revision_id}\\0{offset}\\0{payload_digest}".encode("utf-8")
        ).hexdigest()
        try:
            job_state = await active_transcript_store.start_job(
                job_id, source_id, revision_id, project, file_path, offset, payload_digest,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if job_state["status"] == "completed":
            job = await active_transcript_store.get_job(job_id)
            replay_result = dict((job or {}).get("result") or {})
            replayed_count = int(replay_result.get("inserted", 0)) + int(
                replay_result.get("already_present", 0)
            )
            replay_result["inserted"] = 0
            replay_result["already_present"] = replayed_count
            return {
                **replay_result, "replayed": True, "job_id": job_id, "job": job,
            }
        if job_state["status"] == "running" and not job_state["created"]:
            raise HTTPException(
                status_code=409,
                detail={"message": "transcript slice is already being processed", "job_id": job_id},
            )
        checkpoint = await active_transcript_store.get(source_id, revision_id)
        if checkpoint is not None and offset != checkpoint["byte_offset"]:
            if (
                offset == checkpoint["last_start_offset"]
                and payload_digest == checkpoint["last_payload_sha256"]
            ):
                replay_result = dict(checkpoint["last_result"])
                replayed_count = int(replay_result.get("inserted", 0)) + int(
                    replay_result.get("already_present", 0)
                )
                replay_result["inserted"] = 0
                replay_result["already_present"] = replayed_count
                job = await active_transcript_store.complete_job(
                    job_id, checkpoint["byte_offset"], replay_result,
                )
                return {
                    **replay_result, "replayed": True, "checkpoint": checkpoint,
                    "job_id": job_id, "job": job,
                }
            await active_transcript_store.fail_job(
                job_id, f"checkpoint mismatch; expected byte offset {checkpoint['byte_offset']}",
            )
            raise HTTPException(
                status_code=409,
                detail=f"transcript checkpoint mismatch; expected byte offset {checkpoint['byte_offset']}",
            )
        try:
            parsed = parse_jsonl(raw_content, session_id=session_id, offset=offset)
        except OverflowError as exc:
            await active_transcript_store.fail_job(job_id, str(exc))
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            await active_transcript_store.fail_job(job_id, str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not parsed["messages"]:
            result = {
                "ok": True, "session_id": parsed["session_id"],
                "inserted": 0, "already_present": 0, "messages_received": 0,
                "bytes_consumed": parsed["bytes_consumed"],
                "next_offset": parsed["end_offset"],
                "incomplete_tail": parsed["incomplete_tail"],
                "touches_detected": len(parsed["touches"]),
                "degraded": [],
            }
        else:
            session_payload = {
                "session": {
                    "session_id": parsed["session_id"], "project": project,
                    "file": file_path, "first_ts": parsed["first_ts"],
                    "last_ts": parsed["last_ts"],
                    "is_subagent": bool(payload.get("is_subagent", False)),
                },
                "messages": parsed["messages"],
            }
            try:
                result = await ingest_session(session_payload, request)
            except Exception as exc:
                await active_transcript_store.fail_job(job_id, f"{type(exc).__name__}: {exc}")
                raise
            result.update({
                "bytes_consumed": parsed["bytes_consumed"],
                "next_offset": parsed["end_offset"],
                "incomplete_tail": parsed["incomplete_tail"],
                "touches_detected": len(parsed["touches"]),
            })
        try:
            committed = await active_transcript_store.commit(
                source_id, revision_id, project, file_path, offset, parsed["end_offset"],
                payload_digest, result,
            )
        except ValueError as exc:
            await active_transcript_store.fail_job(job_id, str(exc))
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            await active_transcript_store.fail_job(job_id, f"{type(exc).__name__}: {exc}")
            raise HTTPException(status_code=503, detail="transcript checkpoint persistence unavailable") from exc
        if committed["replayed"]:
            result = committed["result"]
        job = await active_transcript_store.complete_job(job_id, parsed["end_offset"], result)
        return {
            **result, "replayed": committed["replayed"], "source_id": source_id,
            "revision_id": revision_id, "checkpoint": committed["checkpoint"],
            "job_id": job_id, "job": job,
        }

    @app.get("/api/v1/ingest/transcripts/jobs", tags=["ingestion"])
    async def transcript_jobs(limit: int = 50):
        try:
            return {"ok": True, "jobs": await active_transcript_store.list_jobs(limit)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/ingest/transcripts/jobs/{job_id}", tags=["ingestion"])
    async def transcript_job(job_id: str):
        if not re.fullmatch(r"[0-9a-f]{64}", job_id or ""):
            raise HTTPException(status_code=400, detail="job_id must be a lowercase SHA-256 digest")
        job = await active_transcript_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="transcript job not found")
        return {"ok": True, "job": job}

    @app.get("/api/v1/ingest/transcripts/checkpoint", tags=["ingestion"])
    async def transcript_checkpoint(project: str, file: str, revision_id: str = "1"):
        if not project.strip() or len(project) > 256 or not file.strip() or len(file) > 4096:
            raise HTTPException(status_code=400, detail="project and file must be non-empty and within their length limits")
        if not revision_id.strip() or len(revision_id) > 256:
            raise HTTPException(status_code=400, detail="revision_id must be a non-empty string of at most 256 characters")
        source_id = "transcript-file:" + hashlib.sha256(
            f"{project}|{file}".encode("utf-8")
        ).hexdigest()
        checkpoint = await active_transcript_store.get(source_id, revision_id)
        if checkpoint is None:
            raise HTTPException(status_code=404, detail="transcript checkpoint not found")
        return {"ok": True, "checkpoint": checkpoint}

    @app.post("/api/v1/ingest/sessions", tags=["ingestion"])
    async def ingest_session(payload: dict[str, Any], request: Request):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 8 * 1_048_576:
            raise HTTPException(status_code=413, detail="request body exceeds 8 MiB")
        session = payload.get("session")
        messages = payload.get("messages")
        if not isinstance(session, dict):
            raise HTTPException(status_code=400, detail="session must be an object")
        for field, maximum in (("session_id", 256), ("project", 256), ("file", 4096)):
            value = session.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise HTTPException(status_code=400, detail=f"session.{field} must be a non-empty string of at most {maximum} characters")
        if not isinstance(messages, list) or len(messages) > 500:
            raise HTTPException(status_code=400, detail="messages must be a list of at most 500 entries")
        total_chars = 0
        for message in messages:
            if not isinstance(message, dict):
                raise HTTPException(status_code=400, detail="each message must be an object")
            if not isinstance(message.get("uuid"), str) or not message["uuid"] or len(message["uuid"]) > 256:
                raise HTTPException(status_code=400, detail="each message requires a non-empty uuid")
            if message.get("role") not in {"user", "assistant"}:
                raise HTTPException(status_code=400, detail="message role must be user or assistant")
            if message.get("kind", "text") not in {"text", "thinking", "tool_use", "tool_result"}:
                raise HTTPException(status_code=400, detail="message kind is unsupported")
            text_value = message.get("text")
            if not isinstance(text_value, str) or not text_value.strip() or len(text_value) > 200_000:
                raise HTTPException(status_code=400, detail="message text must be non-empty and at most 200000 characters")
            total_chars += len(text_value)
            if total_chars > 4_000_000:
                raise HTTPException(status_code=413, detail="session content exceeds 4000000 characters")
        session_id, project, file_path = session["session_id"], session["project"], session["file"]
        internal = InternalRestClient(app, {
            "graph": os.getenv("ENSEMBLE_GRAPH_REST_URL"),
            "memory": os.getenv("ENSEMBLE_MEMORY_REST_URL"),
            "index": os.getenv("ENSEMBLE_INDEX_REST_URL"),
            "entities": os.getenv("ENSEMBLE_ENTITIES_REST_URL"),
        })
        try:
            session_node = (await internal.request(
                "POST", "/api/v1/graph/nodes", request=request,
                json={"type": "session", "properties": {
                    "session_id": session_id, "project": project,
                    "title": session.get("title"), "file": file_path,
                }},
            ))["node"]
            file_node = (await internal.request(
                "POST", "/api/v1/graph/nodes", request=request,
                json={"type": "file", "properties": {
                    "path": file_path, "session_id": session_id, "project": project,
                }},
            ))["node"]
            await internal.request(
                "POST", "/api/v1/graph/edges", request=request,
                json={"source": session_node["id"], "target": file_node["id"],
                      "type": "contains", "properties": {}},
            )
            inserted = duplicates = 0
            degraded = []
            for message in messages:
                kind = message.get("kind", "text")
                content_revision = hashlib.sha256(message["text"].encode("utf-8")).hexdigest()
                event_id = hashlib.sha256(
                    f"{session_id}|{message['uuid']}|{kind}|{content_revision}".encode("utf-8")
                ).hexdigest()
                event = {
                    "session_id": session_id, "project": project, "file": file_path,
                    "uuid": message["uuid"], "role": message["role"],
                    "kind": kind, "text": message["text"],
                    "timestamp": message.get("ts", ""),
                }
                stored = await internal.request(
                    "PUT",
                    f"/api/v1/memory/memories/session-events/entries/{event_id}",
                    request=request, json={"entry": event},
                )
                source_identity = hashlib.sha256(
                    f"{session_id}|{message['uuid']}|{kind}".encode("utf-8")
                ).hexdigest()
                source_id = f"transcript:{source_identity}"
                source_states = (await internal.request(
                    "POST", "/api/v1/index/sources/query", request=request,
                    json={"source_ids": [source_id]},
                )).get("sources", {})
                source_state = source_states.get(source_id)
                if source_state is None:
                    revision = 1
                elif source_state.get("revision_id") == content_revision:
                    revision = source_state["high_sequence"]
                else:
                    revision = source_state["high_sequence"] + 1
                document = DocumentIn(
                    source_id=source_id,
                    revision=revision,
                    title=f"{project}/{session_id} · {message['role']} · {kind}",
                    content=message["text"],
                    chunk_profile="window-v1",
                    media_type="text/plain",
                    metadata={
                        "session_id": session_id, "project": project,
                        "role": message["role"], "kind": kind,
                        "is_subagent": bool(session.get("is_subagent", False)),
                        "session_file": file_path,
                    },
                )
                ingested, _ = await ingest_indexed_document(
                    document, vector_collection="transcript_text", origin="transcript", locator=file_path,
                    request=request,
                )
                if embeddings.available and not ingested.embedded:
                    degraded.append("embeddings")
                if ingested.embedded and not ingested.vector_indexed:
                    degraded.append("vector")
                if stored["status"] == "duplicate":
                    duplicates += 1
                else:
                    inserted += 1
                extracted = await internal.request(
                    "POST", "/api/v1/entities/extract", request=request,
                    json={"text": message["text"], "source": "transcript"},
                )
                for entity in extracted.get("entities", []):
                    if entity.get("exact") and entity.get("type") in {"path", "file"}:
                        path_node = (await internal.request(
                            "POST", "/api/v1/graph/nodes", request=request,
                            json={"type": "file", "properties": {
                                "path": entity["text"], "session_id": session_id,
                                "project": project,
                            }},
                        ))["node"]
                        await internal.request(
                            "POST", "/api/v1/graph/edges", request=request,
                            json={"source": session_node["id"], "target": path_node["id"],
                                  "type": "touched", "properties": {}},
                        )
            return {
                "ok": True, "session_id": session_id,
                "inserted": inserted, "already_present": duplicates,
                "messages_received": len(messages),
                "degraded": sorted(set(degraded)),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="session ingestion dependency unavailable") from exc

    @app.post("/api/v1/ingest/documents", response_model=DocumentOut, status_code=201, tags=["ingestion"])
    async def ingest_document(document: DocumentIn, http_request: Request):
        try:
            result, _ = await ingest_indexed_document(document, request=http_request)
            return result
        except HTTPException:
            raise
        except ChunkingError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
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
