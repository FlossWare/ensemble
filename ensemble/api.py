from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import random
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, HTMLResponse
from .chunking import Chunker, ChunkingError, PROFILES as CHUNKING_PROFILES, chunk as chunk_text, describe_profiles as describe_chunk_profiles
from .embeddings import EmbeddingProvider, MAX_BODY_BYTES as MAX_EMBED_BODY_BYTES, configured_embedding_provider, validate_embedding_request
from .models import DocumentIn, DocumentOut, SearchIn, SearchHit
from .services import DocumentService, SearchService
from .store import DocumentStore, InMemoryDocumentStore, SqlDocumentStore
from .vector_store import SqliteVectorStore, VectorStore
from .vector_memory import InMemoryVectorStore
from .index_store import IndexStore, SqliteIndexStore
from .index_memory import InMemoryIndexStore
from .knowledge_store import KnowledgeStore, SqliteKnowledgeStore, InMemoryKnowledgeStore
from .graph_store import GraphStore, SqliteGraphStore
from .graph_memory import InMemoryGraphStore
from .tokenization import PROFILES as TOKENIZATION_PROFILES, TokenizationError, describe_profiles, tokenize
from .extraction import extract
from .intent import RULES as INTENT_RULES, classify
from .ranking import DEFAULT_K, fuse
from .reranking import MODEL_ID as RERANK_MODEL_ID, Reranker, RerankingUnavailable, configured_reranker, validate_rerank_request
from .scraping import ScrapeError, preview_uri
from .transcripts import parse_jsonl, MAX_TRANSCRIPT_BYTES
from .file_ingestion import extract_pdf, PDFExtractionError, MAX_PDF_BYTES
from .memory_store import MemoryStore, SqliteMemoryStore, InMemoryMemoryStore
from .ga import GAConfig, GeneticAlgorithm, PARAMETERS as GA_PARAMETERS, SYSTEMS as GA_SYSTEMS
from .ga_store import GAStore, SqliteGAStore, InMemoryGAStore
from .thompson import select_model as thompson_select_model
from .thompson_store import ThompsonStore, SqliteThompsonStore, InMemoryThompsonStore
from .learning_store import LearningStore, SqliteLearningStore, InMemoryLearningStore
from .config import settings_from_env, resolve_database_url


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
) -> FastAPI:
    database_url = resolve_database_url(database_url)
    document_store = store or SqlDocumentStore(database_url)
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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await document_store.initialize()
            await active_vector_store.initialize()
            await active_index_store.initialize()
            await active_knowledge_store.initialize()
            await active_graph_store.initialize()
            await active_memory_store.initialize()
            await active_ga_store.initialize()
            await active_ga_store.recover_interrupted_runs()
            await active_thompson_store.initialize()
            await active_learning_store.initialize()
            yield
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
    documents = DocumentService(document_store, Chunker(), embeddings, active_vector_store)
    search = SearchService(document_store, embeddings, active_reranker, active_vector_store)

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
            "host": settings.host,
            "port": settings.port,
            "persistence_root": str(settings.persistence_root),
            "database": "sqlite",
            "database_url": settings.database_url,
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
            "capabilities": ["documents", "search", "lexical", "hybrid", "chunking", "embeddings", "vector", "index", "knowledge", "tokenization", "extraction", "intent", "ranking", "reranking", "graph", "memory", "ga", "thompson", "learning"],
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
        return {
            "ok": True, "decision": decision["decision"], "source_id": source_id,
            "stored": True, "content_sha256": digest,
        }

    @app.get("/api/v1/knowledge/records/{source_id}", tags=["knowledge"])
    async def knowledge_get(source_id: str):
        record = await active_knowledge_store.get(source_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown record")
        return {"ok": True, "record": record}

    @app.post("/api/v1/knowledge/records/{source_id}/tombstone", tags=["knowledge"])
    async def knowledge_tombstone(source_id: str, payload: dict[str, Any]):
        sequence = payload.get("revision_sequence")
        if not source_id or len(source_id) > 512:
            raise HTTPException(status_code=400, detail="source_id must be a non-empty string of at most 512 characters")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise HTTPException(status_code=400, detail="revision_sequence must be a positive integer")
        decision = await active_index_store.record_tombstone(source_id, sequence)
        if decision["decision"] == "tombstoned":
            await active_knowledge_store.tombstone(source_id)
        return {"ok": True, "decision": decision["decision"], "source_id": source_id}

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
    async def index_tombstone(payload: dict[str, Any]):
        source_id = payload.get("source_id")
        sequence = payload.get("revision_sequence")
        if not isinstance(source_id, str) or not source_id or len(source_id) > 512:
            raise HTTPException(status_code=400, detail="source_id must be a non-empty string of at most 512 characters")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise HTTPException(status_code=400, detail="revision_sequence must be a positive integer")
        result = await active_index_store.record_tombstone(source_id, sequence)
        return {"ok": True, **result}

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

    @app.post("/api/v1/vector/collections/{name}/existing", tags=["vector"])
    async def vector_existing(name: str, payload: dict[str, Any]):
        _validate_vector_collection_name(name)
        ids = payload.get("ids", [])
        if not isinstance(ids, list) or len(ids) > 1000 or any(
            not isinstance(item, str) or not item or len(item) > 512 for item in ids
        ):
            raise HTTPException(status_code=400, detail="ids must be a list of at most 1000 non-empty strings")
        return {"ok": True, "found": await active_vector_store.existing(name, ids)}

    @app.post("/api/v1/vector/collections/{name}/upsert", tags=["vector"])
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
            count = await active_vector_store.upsert(name, model, dimensions, items)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "upserted": count}

    @app.post("/api/v1/vector/collections/{name}/metadata", tags=["vector"])
    async def vector_metadata(name: str, payload: dict[str, Any]):
        _validate_vector_collection_name(name)
        ids, metadatas = payload.get("ids"), payload.get("metadatas")
        if not isinstance(ids, list) or not isinstance(metadatas, list) or not ids or len(ids) != len(metadatas) or len(ids) > 256:
            raise HTTPException(status_code=400, detail="ids and metadatas must be non-empty lists of equal length, at most 256")
        if any(not isinstance(item, str) or not item for item in ids) or any(not isinstance(item, dict) for item in metadatas):
            raise HTTPException(status_code=400, detail="ids must be strings and metadatas must be objects")
        updated = await active_vector_store.update_metadata(name, [
            {"id": record_id, "metadata": metadata} for record_id, metadata in zip(ids, metadatas)
        ])
        return {"ok": True, "updated": updated}

    @app.post("/api/v1/vector/collections/{name}/delete-superseded", tags=["vector"])
    async def vector_delete_superseded(name: str, payload: dict[str, Any]):
        _validate_vector_collection_name(name)
        url, digest = payload.get("url"), payload.get("keep_sha256")
        if not isinstance(url, str) or not url or len(url) > 2048:
            raise HTTPException(status_code=400, detail="url must be a non-empty string of at most 2048 characters")
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise HTTPException(status_code=400, detail="keep_sha256 must be a lowercase hex SHA-256")
        deleted = await active_vector_store.delete_superseded(name, url, digest)
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
        return {"ok": True, "results": await active_vector_store.query(name, vector, top_k)}

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

    @app.get("/api/v1/extraction/health", tags=["extraction"])
    async def extraction_health():
        return {"ok": True, "service": "extraction"}

    @app.get("/api/v1/extraction/ready", tags=["extraction"])
    async def extraction_ready():
        return {"ok": True, "ready": True}

    @app.post("/api/v1/extraction/extract", tags=["extraction"])
    async def extract_entities(payload: dict[str, Any]):
        try:
            entities = extract(payload.get("text"))
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "entities": entities}

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

    @app.get("/api/v1/reranking/health", tags=["reranking"])
    async def reranking_health():
        status = active_reranker.status()
        return JSONResponse(status_code=200 if active_reranker.available else 503, content=status)

    @app.get("/api/v1/reranking/ready", tags=["reranking"])
    async def reranking_ready():
        status = active_reranker.status()
        return JSONResponse(status_code=200 if active_reranker.available else 503, content=status)

    @app.post("/api/v1/reranking/rerank", tags=["reranking"])
    async def rerank_candidates(payload: dict[str, Any]):
        query, documents = payload.get("query"), payload.get("documents")
        try:
            validate_rerank_request(query, documents)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            scores = await asyncio.to_thread(active_reranker.score, query, documents)
        except RerankingUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True, "model": RERANK_MODEL_ID, "scores": scores}

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


    @app.post("/api/v1/memory/search-semantic", tags=["memory"])
    async def memory_search_semantic(payload: dict[str, Any]):
        try:
            top_k = payload.get("top_k", payload.get("limit", 10))
            result = await active_memory_store.search_semantic(
                payload.get("query", ""), top_k, payload.get("source")
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "results": result}

    @app.post("/api/v1/memory/search-hybrid", tags=["memory"])
    async def memory_search_hybrid(payload: dict[str, Any]):
        try:
            top_k = payload.get("top_k", payload.get("limit", 10))
            result = await active_memory_store.search_hybrid(payload.get("query", ""), top_k)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "results": result}

    @app.post("/api/v1/memory/ingest", tags=["memory"])
    async def memory_ingest(payload: dict[str, Any]):
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

    @app.post("/api/v1/memory/reconcile", tags=["memory"])
    async def memory_reconcile(payload: dict[str, Any]):
        try:
            result = await active_memory_store.reconcile_claude_markdown(
                payload.get("source", ""), payload.get("scope", ""), payload.get("paths", [])
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.get("/api/v1/memory/knowledge/status", tags=["memory"])
    async def memory_knowledge_status():
        return {"ok": True, **(await active_memory_store.knowledge_status())}

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

    @app.post("/api/v1/memory/read", tags=["memory"])
    async def memory_read(payload: dict[str, Any]):
        try:
            content = await active_memory_store.read(payload.get("name"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if content is None:
            raise HTTPException(status_code=404, detail="memory not found")
        return {"ok": True, "content": content}

    @app.post("/api/v1/memory/write", tags=["memory"])
    async def memory_write(payload: dict[str, Any]):
        try:
            await active_memory_store.write(payload.get("name"), payload.get("content"))
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/v1/memory/append", tags=["memory"])
    async def memory_append(payload: dict[str, Any]):
        try:
            result = await active_memory_store.append(payload.get("name"), payload.get("entry", {}))
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.post("/api/v1/memory/append-once", tags=["memory"])
    async def memory_append_once(payload: dict[str, Any]):
        try:
            result = await active_memory_store.append(
                payload.get("name"), payload.get("entry", {}), payload.get("event_id")
            )
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409 if "already exists" in str(exc) else 400, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.post("/api/v1/memory/entries", tags=["memory"])
    async def memory_entries(payload: dict[str, Any]):
        try:
            result = await active_memory_store.read_entries(payload.get("name"), payload.get("limit", 1000))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "entries": result}

    @app.post("/api/v1/memory/retrieve", tags=["memory"])
    async def memory_retrieve(payload: dict[str, Any]):
        try:
            result = await active_memory_store.retrieve(
                payload.get("name"), payload.get("context", {}), payload.get("limit", 10)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "results": result}

    @app.get("/api/v1/memory/list", tags=["memory"])
    async def memory_list():
        return {"ok": True, "memories": await active_memory_store.list_names()}

    @app.post("/api/v1/memory/search", tags=["memory"])
    async def memory_search(payload: dict[str, Any]):
        try:
            result = await active_memory_store.search(payload.get("keywords", []), payload.get("limit", 50))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "results": result}

    @app.post("/api/v1/memory/chunk", tags=["memory"])
    async def memory_chunk(payload: dict[str, Any]):
        try:
            result = await active_memory_store.chunk(payload.get("name"), payload.get("size", 2000))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "chunks": result}

    @app.get("/api/v1/graph/health", tags=["graph"])
    async def graph_health():
        return {"ok": True, "service": "graph", "backend": "sqlite"}

    @app.post("/api/v1/graph/add-node", tags=["graph"])
    async def graph_add_node(payload: dict[str, Any]):
        try:
            node = await active_graph_store.add_node(payload.get("type"), payload.get("properties", {}), payload.get("id"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "node": node}

    @app.post("/api/v1/graph/add-edge", tags=["graph"])
    async def graph_add_edge(payload: dict[str, Any]):
        try:
            edge = await active_graph_store.add_edge(payload.get("source"), payload.get("target"), payload.get("type"), payload.get("properties", {}), payload.get("id"))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "edge": edge}

    @app.get("/api/v1/graph/node/{node_id}", tags=["graph"])
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
            result = await documents.ingest(document)
            return {"ok": True, "document": result, "extraction": {
                "page_count": extracted["page_count"],
                "extracted_pages": extracted["extracted_pages"],
            }}
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except PDFExtractionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    async def scrape_and_ingest(payload: dict[str, Any]):
        try:
            preview = await preview_uri(payload.get("uri"))
            source_id = preview["final_uri"]
            current = await active_index_store.get_source(source_id)
            revision = payload.get("revision")
            if revision is None:
                revision = (current["high_sequence"] + 1) if current else 1
            digest = hashlib.sha256(preview["text"].encode("utf-8")).hexdigest()
            profiles = {"chunking": "legacy-char-window-1200-160", "embedding": embeddings.status().get("model") or "disabled"}
            decision = await active_index_store.record_revision(source_id, revision, digest, profiles)
            if decision["decision"] not in {"accepted", "duplicate"}:
                raise HTTPException(status_code=409, detail="source revision rejected: " + decision["decision"])
            document = DocumentIn(source_id=source_id, revision=revision, title=preview["title"], content=preview["text"])
            result = await documents.ingest(document)
            await active_knowledge_store.put({
                "source_id": source_id, "revision_sequence": revision, "revision_id": digest,
                "title": preview["title"], "content": preview["text"], "origin": "web",
                "locator": source_id, "profiles": profiles,
            })
            if embeddings.available:
                indexed = await document_store.all_chunks()
                records = [item for item in indexed if item.document.id == result.id and item.chunk.embedding]
                if records:
                    status = embeddings.status()
                    dimensions = status.get("dimensions") or len(records[0].chunk.embedding)
                    await active_vector_store.upsert("documents", status.get("model") or "configured", dimensions, [
                        {"id": str(item.chunk.id), "embedding": item.chunk.embedding, "document": item.chunk.content,
                         "metadata": {"url": source_id, "source_id": source_id, "content_sha256": digest,
                                      "revision": revision, "title": preview["title"]}}
                        for item in records
                    ])
            return {"ok": True, "decision": decision["decision"], "source": preview, "document": result}
        except ScrapeError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="scrape ingestion failed") from exc

    @app.get("/api/v1/search/lexical/health", tags=["lexical"])
    async def lexical_health():
        return {"ok": True, "service": "lexical", "backend": "document-store"}

    @app.get("/api/v1/search/lexical/ready", tags=["lexical"])
    async def lexical_ready():
        try:
            await document_store.check_ready()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="lexical document store unavailable") from exc
        return {"ok": True, "ready": True}

    @app.post("/api/v1/lexical/documents", status_code=201, tags=["lexical"])
    async def lexical_ingest_document(payload: dict[str, Any], request: Request, response: Response):
        # The POC contract accepts a session plus messages. The document form is
        # retained as a convenience for callers that already have a document.
        if "session" in payload or "messages" in payload:
            response.status_code = 200
            return await hybrid_ingest_session(payload, request)
        try:
            document_request = DocumentIn.model_validate(payload)
            return await documents.ingest(document_request, embed=False)
        except ChunkingError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="lexical document ingestion failed") from exc

    @app.post("/api/v1/search/lexical", tags=["lexical"])
    async def lexical_search(request: SearchIn):
        try:
            results = await search.lexical_search(request.query, request.limit)
            return {"ok": True, "results": results, "retrieval": "lexical"}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/search/hybrid/health", tags=["hybrid"])
    async def hybrid_health():
        return {"ok": True, "service": "hybrid"}

    @app.get("/api/v1/search/hybrid/ready", tags=["hybrid"])
    async def hybrid_ready():
        try:
            await document_store.check_ready()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="hybrid retrieval persistence unavailable") from exc
        return {
            "ok": True, "ready": True,
            "embedding_available": embeddings.available,
            "degraded_mode": "lexical fallback",
        }

    @app.post("/api/v1/search/hybrid", tags=["hybrid"])
    async def hybrid_search(request: SearchIn):
        try:
            result = await search.search_with_status(request.query, request.limit, rerank=request.rerank)
            intent = classify(request.query)
            result["intent"] = {"label": intent["label"], "confidence": intent["confidence"]}
            related = []
            try:
                exact_paths = {
                    item["text"] for item in extract(request.query)
                    if item.get("exact") and item.get("type") in {"path", "file"}
                }
                if exact_paths:
                    file_nodes = await active_graph_store.query_nodes("file", {})
                    sessions = {}
                    for file_node in file_nodes:
                        path = (file_node.get("properties") or {}).get("path", "")
                        if not isinstance(path, str) or not any(
                            path == candidate or path.endswith("/" + candidate)
                            for candidate in exact_paths
                        ):
                            continue
                        for hit in await active_graph_store.traverse(
                            file_node["id"], direction="in", max_depth=1, edge_type="touched"
                        ):
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
            # Successful searches are recorded for audit/learning. Ledger failure
            # must never turn a successful retrieval into a failed request.
            try:
                await active_memory_store.append(
                    "search-ledger",
                    {
                        "query": request.query,
                        "intent": result["intent"],
                        "degraded": result["degraded"],
                        "result_count": len(result["results"]),
                        "results": [{
                            "source_id": hit.source_id,
                            "revision": hit.revision,
                            "chunk_id": str(hit.chunk_id),
                            "score": hit.fusion_score,
                        } for hit in result["results"]],
                    },
                    event_id=f"search-{uuid4()}",
                )
            except Exception:
                pass
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="hybrid search failed") from exc

    @app.post("/api/v1/search/hybrid/knowledge", tags=["hybrid"])
    async def hybrid_knowledge_search(request: SearchIn):
        if not embeddings.available:
            raise HTTPException(status_code=503, detail="knowledge search requires an available embedding provider")
        try:
            vector = (await embeddings.embed([request.query], mode="query"))[0]
            results = await active_vector_store.query("knowledge_chunks", vector, request.limit)
            return {
                "ok": True, "results": results, "degraded": [],
                "retrieval": "knowledge-only",
            }
        except Exception as exc:
            raise HTTPException(status_code=503, detail="knowledge retrieval dependency unavailable") from exc

    @app.post("/api/v1/ingest/transcripts", tags=["ingestion"])
    async def hybrid_ingest_transcript(payload: dict[str, Any], request: Request):
        """Ingest a caller-supplied JSONL byte slice; never opens a server-side path."""
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_TRANSCRIPT_BYTES + 65_536:
            raise HTTPException(status_code=413, detail="request body exceeds transcript limit")
        raw_content = payload.get("content")
        session_id = payload.get("session_id")
        project = payload.get("project")
        file_path = payload.get("file", "transcript.jsonl")
        offset = payload.get("offset", 0)
        if not isinstance(project, str) or not project.strip() or len(project) > 256:
            raise HTTPException(status_code=400, detail="project must be a non-empty string of at most 256 characters")
        if not isinstance(file_path, str) or not file_path.strip() or len(file_path) > 4096:
            raise HTTPException(status_code=400, detail="file must be a non-empty logical source label of at most 4096 characters")
        try:
            parsed = parse_jsonl(raw_content, session_id=session_id, offset=offset)
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not parsed["messages"]:
            return {
                "ok": True, "session_id": parsed["session_id"],
                "inserted": 0, "already_present": 0, "messages_received": 0,
                "bytes_consumed": parsed["bytes_consumed"],
                "next_offset": parsed["end_offset"],
                "incomplete_tail": parsed["incomplete_tail"],
                "degraded": [],
            }
        session_payload = {
            "session": {
                "session_id": parsed["session_id"], "project": project,
                "file": file_path, "first_ts": parsed["first_ts"],
                "last_ts": parsed["last_ts"],
                "is_subagent": bool(payload.get("is_subagent", False)),
            },
            "messages": parsed["messages"],
        }
        result = await hybrid_ingest_session(session_payload, request)
        result.update({
            "bytes_consumed": parsed["bytes_consumed"],
            "next_offset": parsed["end_offset"],
            "incomplete_tail": parsed["incomplete_tail"],
            "touches_detected": len(parsed["touches"]),
        })
        return result

    @app.post("/api/v1/hybrid/sessions/ingest", tags=["hybrid"])
    async def hybrid_ingest_session(payload: dict[str, Any], request: Request):
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
        try:
            session_node = await active_graph_store.add_node("session", {
                "session_id": session_id, "project": project,
                "title": session.get("title"), "file": file_path,
            })
            file_node = await active_graph_store.add_node("file", {
                "path": file_path, "session_id": session_id, "project": project,
            })
            await active_graph_store.add_edge(
                session_node["id"], file_node["id"], "contains", {}
            )
            inserted = duplicates = 0
            degraded = []
            for message in messages:
                kind = message.get("kind", "text")
                event_id = hashlib.sha256(
                    f"{session_id}|{message['uuid']}|{kind}|{message['text']}".encode("utf-8")
                ).hexdigest()
                event = {
                    "session_id": session_id, "project": project, "file": file_path,
                    "uuid": message["uuid"], "role": message["role"],
                    "kind": kind, "text": message["text"],
                    "timestamp": message.get("ts", ""),
                }
                stored = await active_memory_store.append("session-events", event, event_id=event_id)
                source_digest = hashlib.sha256(
                    f"{message['uuid']}|{kind}|{message['text']}".encode("utf-8")
                ).hexdigest()
                document = DocumentIn(
                    source_id=f"transcript:{session_id}:{source_digest}",
                    revision=1,
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
                ingested = await documents.ingest(
                    document, vector_collection="transcript_text"
                )
                if embeddings.available and not ingested.embedded:
                    degraded.append("embeddings")
                if ingested.embedded and not ingested.vector_indexed:
                    degraded.append("vector")
                if ingested.embedded and not ingested.vector_indexed:
                    degraded.append("vector")
                if stored["status"] == "duplicate":
                    duplicates += 1
                else:
                    inserted += 1
                for entity in extract(message["text"]):
                    if entity.get("exact") and entity.get("type") in {"path", "file"}:
                        path_node = await active_graph_store.add_node("file", {
                            "path": entity["text"], "session_id": session_id,
                            "project": project,
                        })
                        await active_graph_store.add_edge(
                            session_node["id"], path_node["id"], "touched", {}
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

    @app.post("/api/v1/hybrid/prompts", tags=["hybrid"])
    async def hybrid_capture_prompt(payload: dict[str, Any], request: Request):
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

    @app.post("/api/v1/ingest/documents", response_model=DocumentOut, status_code=201, tags=["ingestion"])
    async def ingest_document(request: DocumentIn):
        try:
            return await documents.ingest(request)
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
