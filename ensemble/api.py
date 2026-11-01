from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .chunking import Chunker, ChunkingError, PROFILES as CHUNKING_PROFILES, chunk as chunk_text, describe_profiles as describe_chunk_profiles
from .embeddings import EmbeddingProvider, MAX_BODY_BYTES as MAX_EMBED_BODY_BYTES, configured_embedding_provider, validate_embedding_request
from .models import DocumentIn, DocumentOut, SearchIn, SearchHit
from .services import DocumentService, SearchService
from .store import DocumentStore, InMemoryDocumentStore, SqlDocumentStore
from .vector_store import SqliteVectorStore, VectorStore
from .vector_memory import InMemoryVectorStore
from .tokenization import PROFILES as TOKENIZATION_PROFILES, TokenizationError, describe_profiles, tokenize
from .extraction import extract
from .intent import RULES as INTENT_RULES, classify
from .ranking import DEFAULT_K, fuse
from .reranking import MODEL_ID as RERANK_MODEL_ID, Reranker, RerankingUnavailable, configured_reranker, validate_rerank_request


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
) -> FastAPI:
    document_store = store or SqlDocumentStore(database_url)
    embeddings = embedding_provider or configured_embedding_provider()
    active_reranker = reranker or configured_reranker()
    active_vector_store = vector_store or (
        InMemoryVectorStore() if isinstance(document_store, InMemoryDocumentStore)
        else SqliteVectorStore(database_url)
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await document_store.initialize()
            await active_vector_store.initialize()
            yield
        finally:
            await active_vector_store.close()
            await document_store.close()

    app = FastAPI(
        title="Ensemble API",
        version="0.1",
        description="REST-first memory, knowledge, and retrieval platform.",
        lifespan=lifespan,
    )
    documents = DocumentService(document_store, Chunker(), embeddings)
    search = SearchService(document_store, embeddings)

    app.state.store = document_store
    app.state.embedding_provider = embeddings
    app.state.reranker = active_reranker
    app.state.vector_store = active_vector_store

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

    @app.get("/health", tags=["operations"])
    async def health():
        return {"status": "ok", "service": "ensemble"}

    @app.get("/ready", tags=["operations"])
    async def ready():
        try:
            await document_store.check_ready()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="persistence unavailable") from exc
        return {
            "status": "ready",
            "persistence": "durable",
            "embeddings": embeddings.available,
            "capabilities": ["documents", "search", "chunking", "embeddings", "vector", "tokenization", "extraction", "intent", "ranking", "reranking"],
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
        ids = payload.get("ids", [])
        if not isinstance(ids, list) or len(ids) > 1000 or any(
            not isinstance(item, str) or not item or len(item) > 512 for item in ids
        ):
            raise HTTPException(status_code=400, detail="ids must be a list of at most 1000 non-empty strings")
        return {"ok": True, "found": await active_vector_store.existing(name, ids)}

    @app.post("/api/v1/vector/collections/{name}/upsert", tags=["vector"])
    async def vector_upsert(name: str, payload: dict[str, Any]):
        ids, vectors = payload.get("ids"), payload.get("embeddings")
        documents_payload, metadatas = payload.get("documents"), payload.get("metadatas")
        if not isinstance(ids, list) or not isinstance(vectors, list) or not isinstance(documents_payload, list):
            raise HTTPException(status_code=400, detail="ids, embeddings, and documents must be lists")
        if not ids or len(ids) != len(vectors) or len(ids) != len(documents_payload):
            raise HTTPException(status_code=400, detail="ids, embeddings, and documents must be non-empty and equal length")
        if len(ids) > 256:
            raise HTTPException(status_code=413, detail="at most 256 records per upsert")
        if len(set(ids)) != len(ids) or any(not isinstance(item, str) or not item or len(item) > 512 for item in ids):
            raise HTTPException(status_code=400, detail="ids must be unique non-empty strings of at most 512 characters")
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
        url, digest = payload.get("url"), payload.get("keep_sha256")
        if not isinstance(url, str) or not url or len(url) > 2048:
            raise HTTPException(status_code=400, detail="url must be a non-empty string of at most 2048 characters")
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise HTTPException(status_code=400, detail="keep_sha256 must be a lowercase hex SHA-256")
        deleted = await active_vector_store.delete_superseded(name, url, digest)
        return {"ok": True, "deleted": deleted}

    @app.post("/api/v1/vector/collections/{name}/query", tags=["vector"])
    async def vector_query(name: str, payload: dict[str, Any]):
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

    @app.post("/api/v1/documents", response_model=DocumentOut, status_code=201, tags=["documents"])
    async def ingest_document(request: DocumentIn):
        try:
            return await documents.ingest(request)
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
