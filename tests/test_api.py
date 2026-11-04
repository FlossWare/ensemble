import json
import asyncio
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.store import InMemoryDocumentStore, SqlDocumentStore
from ensemble.graph_memory import InMemoryGraphStore
from ensemble.vector_memory import InMemoryVectorStore
from ensemble.memory_store import InMemoryMemoryStore
from ensemble.knowledge_store import InMemoryKnowledgeStore, SqliteKnowledgeStore
from ensemble.index_store import SqliteIndexStore
from ensemble.index_memory import InMemoryIndexStore
from ensemble.models import Document, Chunk
from ensemble.rebuild import IndexGenerationBuilder
from ensemble.vector_store import SqliteVectorStore


def _client(tmp_path):
    database = tmp_path / "ensemble.db"
    database_url = f"sqlite+aiosqlite:///{database}"
    migration_config = Config("alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")
    return TestClient(create_app(database_url=database_url))


def test_startup_rejects_unmigrated_database(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'unmigrated.db'}"
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        with TestClient(create_app(database_url=database_url)):
            pass


def test_health_and_ready(tmp_path):
    with _client(tmp_path) as client:
        assert client.get("/health").json()["status"] == "ok"
        ready = client.get("/ready").json()
        assert ready["status"] == "ready"
        assert ready["persistence"] == "durable"


def test_ingest_then_lexical_search_returns_provenance(tmp_path):
    with _client(tmp_path) as client:
        response = client.post("/api/v1/ingest/documents", json={
            "source_id": "architecture-note",
            "revision": 1,
            "title": "Vector retrieval",
            "content": "Vector retrieval combines embeddings with lexical search for better recall."
        })
        assert response.status_code == 201
        document = response.json()
        assert document["chunk_count"] == 1
        assert document["embedded"] is False

        result = client.post("/api/v1/search", json={"query": "lexical embeddings", "limit": 5})
        assert result.status_code == 200
        hits = result.json()
        assert len(hits) == 1
        assert hits[0]["source_id"] == "architecture-note"
        assert hits[0]["document_id"] == document["id"]
        assert hits[0]["lexical_score"] > 0


def test_validation_errors_use_problem_details(tmp_path):
    with _client(tmp_path) as client:
        response = client.post("/api/v1/ingest/documents", json={})
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["status"] == 422
        assert body["code"] == "request-validation-failed"
        assert body["correlationId"]


def test_documents_survive_application_restart(tmp_path):
    payload = {
        "source_id": "persistent-note",
        "revision": 1,
        "title": "Durable storage",
        "content": "Durable storage must survive application restarts and process replacement.",
    }
    with _client(tmp_path) as client:
        first = client.post("/api/v1/ingest/documents", json=payload).json()

    with _client(tmp_path) as client:
        hits = client.post("/api/v1/search", json={"query": "durable storage"}).json()
        assert len(hits) == 1
        assert hits[0]["document_id"] == first["id"]


def test_reingest_replaces_chunk_content_for_same_revision(tmp_path):
    with _client(tmp_path) as client:
        original = {
            "source_id": "mutable-source",
            "revision": 1,
            "title": "Mutable source",
            "content": "The original content contains obsolete terminology.",
        }
        first = client.post("/api/v1/ingest/documents", json=original).json()
        replacement = {
            **original,
            "revision": 2,
            "content": "The replacement content contains current terminology.",
        }
        second_response = client.post("/api/v1/ingest/documents", json=replacement)
        assert second_response.status_code == 201
        second = second_response.json()
        assert second["id"] != first["id"]

        old_hits = client.post("/api/v1/search", json={"query": "obsolete"}).json()
        new_hits = client.post("/api/v1/search", json={"query": "current terminology"}).json()
        assert old_hits == []
        assert len(new_hits) == 1
        assert new_hits[0]["revision"] == 2
        assert new_hits[0]["text"] == replacement["content"]


def test_reingest_is_idempotent_and_latest_revision_is_searchable(tmp_path):
    with _client(tmp_path) as client:
        original = {
            "source_id": "versioned-note",
            "revision": 1,
            "title": "First revision",
            "content": "The original document discusses obsolete indexing behavior.",
        }
        first = client.post("/api/v1/ingest/documents", json=original).json()
        again = client.post("/api/v1/ingest/documents", json=original).json()
        assert again["id"] == first["id"]

        revised = {
            "source_id": "versioned-note",
            "revision": 2,
            "title": "Second revision",
            "content": "The current document discusses durable indexing behavior.",
        }
        second = client.post("/api/v1/ingest/documents", json=revised).json()
        assert second["id"] != first["id"]

        current = client.post("/api/v1/search", json={"query": "indexing behavior"}).json()
        assert len(current) == 1
        assert current[0]["revision"] == 2
        assert current[0]["document_id"] == second["id"]

        historical = client.post("/api/v1/ingest/documents", json=original)
        assert historical.status_code == 409
        assert historical.json()["status"] == 409
        still_current = client.post("/api/v1/search", json={"query": "indexing behavior"}).json()
        assert len(still_current) == 1
        assert still_current[0]["revision"] == 2



def test_vector_collection_upsert_query_and_existing_ids(tmp_path):
    with _client(tmp_path) as client:
        payload = {
            "model": "test-embedding-model",
            "dimensions": 3,
            "ids": ["doc-a", "doc-b"],
            "embeddings": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            "documents": ["alpha document", "beta document"],
            "metadatas": [
                {"url": "https://example.test/a", "content_sha256": "a" * 64},
                {"url": "https://example.test/b", "content_sha256": "b" * 64},
            ],
        }
        upsert = client.put("/api/v1/vector/collections/knowledge/records", json=payload)
        assert upsert.status_code == 200
        assert upsert.json()["upserted"] == 2

        existing = client.post(
            "/api/v1/vector/collections/knowledge/records/lookup",
            json={"ids": ["doc-b", "missing", "doc-a"]},
        )
        assert existing.json()["found"] == ["doc-b", "doc-a"]

        query = client.post(
            "/api/v1/vector/collections/knowledge/query",
            json={"embedding": [0.0, 1.0, 0.0], "top_k": 2},
        )
        assert query.status_code == 200
        assert [item["id"] for item in query.json()["results"]] == ["doc-b", "doc-a"]
        assert query.json()["results"][0]["score"] == 1.0


def test_vector_upsert_is_idempotent_and_model_mismatch_fails(tmp_path):
    with _client(tmp_path) as client:
        base = {
            "model": "model-a", "dimensions": 2,
            "ids": ["same-id"], "embeddings": [[1.0, 0.0]],
            "documents": ["original"], "metadatas": [{"version": 1}],
        }
        assert client.put("/api/v1/vector/collections/c/records", json=base).status_code == 200
        replacement = {**base, "documents": ["replacement"], "metadatas": [{"version": 2}]}
        assert client.put("/api/v1/vector/collections/c/records", json=replacement).json()["upserted"] == 1
        result = client.post(
            "/api/v1/vector/collections/c/query",
            json={"embedding": [1.0, 0.0]},
        ).json()["results"]
        assert len(result) == 1
        assert result[0]["document"] == "replacement"

        mismatch = {**base, "model": "model-b"}
        assert client.put("/api/v1/vector/collections/c/records", json=mismatch).status_code == 409


def test_vector_metadata_and_delete_superseded(tmp_path):
    with _client(tmp_path) as client:
        payload = {
            "model": "model-a", "dimensions": 2,
            "ids": ["old", "current"], "embeddings": [[1.0, 0.0], [0.0, 1.0]],
            "documents": ["old", "current"],
            "metadatas": [
                {"url": "https://example.test/source", "content_sha256": "a" * 64},
                {"url": "https://example.test/source", "content_sha256": "b" * 64},
            ],
        }
        client.put("/api/v1/vector/collections/c/records", json=payload)
        updated = client.patch(
            "/api/v1/vector/collections/c/records/metadata",
            json={"ids": ["current", "missing"], "metadatas": [{"tag": "new"}, {"tag": "none"}]},
        )
        assert updated.json()["updated"] == 1
        deleted = client.post(
            "/api/v1/vector/collections/c/records/prune",
            json={"url": "https://example.test/source", "keep_sha256": "b" * 64},
        )
        assert deleted.json()["deleted"] == 1
        found = client.post(
            "/api/v1/vector/collections/c/records/lookup",
            json={"ids": ["old", "current"]},
        ).json()["found"]
        assert found == ["current"]


def test_vector_collection_persists_across_restart(tmp_path):
    payload = {
        "model": "model-a", "dimensions": 2,
        "ids": ["persistent"], "embeddings": [[0.5, 0.5]],
        "documents": ["persisted vector"], "metadatas": [{"source": "test"}],
    }
    with _client(tmp_path) as client:
        assert client.put("/api/v1/vector/collections/c/records", json=payload).status_code == 200

    with _client(tmp_path) as client:
        result = client.post(
            "/api/v1/vector/collections/c/query",
            json={"embedding": [0.5, 0.5]},
        ).json()["results"]
        assert result[0]["id"] == "persistent"


def test_vector_contract_rejects_invalid_vectors(tmp_path):
    with _client(tmp_path) as client:
        invalid = '{"model":"model-a","dimensions":2,"ids":["bad"],"embeddings":[[NaN,0.0]],"documents":["bad"],"metadatas":[{"source":"test"}]}'
        response = client.put(
            "/api/v1/vector/collections/c/records",
            content=invalid,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
        response = client.post(
            "/api/v1/vector/collections/c/query",
            json={"embedding": [1.0], "top_k": 1},
        )
        assert response.status_code == 400



def test_index_revision_decisions_and_profile_conflicts(tmp_path):
    with _client(tmp_path) as client:
        first = client.post("/api/v1/index/revisions", json={
            "source_id": "source-a", "revision_sequence": 1,
            "revision_id": "rev-1", "profiles": {"chunk": "window-v1"},
        }).json()
        assert first["decision"] == "accepted"

        duplicate = client.post("/api/v1/index/revisions", json={
            "source_id": "source-a", "revision_sequence": 1,
            "revision_id": "rev-1", "profiles": {"chunk": "window-v1"},
        }).json()
        assert duplicate["decision"] == "duplicate"

        conflict = client.post("/api/v1/index/revisions", json={
            "source_id": "source-a", "revision_sequence": 1,
            "revision_id": "rev-1", "profiles": {"chunk": "markdown-v1"},
        }).json()
        assert conflict["decision"] == "conflict"

        newer = client.post("/api/v1/index/revisions", json={
            "source_id": "source-a", "revision_sequence": 2,
            "revision_id": "rev-2", "profiles": {"chunk": "markdown-v1"},
        }).json()
        assert newer["decision"] == "accepted"

        late = client.post("/api/v1/index/revisions", json={
            "source_id": "source-a", "revision_sequence": 1,
            "revision_id": "rev-1", "profiles": {"chunk": "window-v1"},
        }).json()
        assert late["decision"] == "superseded"


def test_index_tombstones_are_permanent_and_manifest_is_bounded(tmp_path):
    with _client(tmp_path) as client:
        client.post("/api/v1/index/revisions", json={
            "source_id": "source-b", "revision_sequence": 3,
            "revision_id": "rev-3", "profiles": {"chunk": "window-v1"},
        })
        tombstone = client.post("/api/v1/index/tombstones", json={
            "source_id": "source-b", "revision_sequence": 4,
        }).json()
        assert tombstone["decision"] == "tombstoned"

        later = client.post("/api/v1/index/revisions", json={
            "source_id": "source-b", "revision_sequence": 5,
            "revision_id": "rev-5", "profiles": {"chunk": "window-v1"},
        }).json()
        assert later["decision"] == "tombstoned"
        source = client.get("/api/v1/index/sources/source-b").json()["source"]
        assert source["tombstoned"] is True
        assert source["high_sequence"] == 4

        assert client.get("/api/v1/index/manifest?limit=500").status_code == 200
        assert client.get("/api/v1/index/manifest?limit=501").status_code == 400


def test_index_manifest_persists_across_restart(tmp_path):
    payload = {
        "source_id": "persistent-index", "revision_sequence": 9,
        "revision_id": "rev-9", "profiles": {"chunk": "window-v1"},
    }
    with _client(tmp_path) as client:
        assert client.post("/api/v1/index/revisions", json=payload).json()["decision"] == "accepted"

    with _client(tmp_path) as client:
        source = client.get("/api/v1/index/sources/persistent-index").json()["source"]
        assert source["high_sequence"] == 9
        assert source["revision_id"] == "rev-9"


def test_index_rejects_invalid_revision_inputs(tmp_path):
    with _client(tmp_path) as client:
        response = client.post("/api/v1/index/revisions", json={
            "source_id": "source", "revision_sequence": True,
            "revision_id": "rev", "profiles": {"chunk": "window-v1"},
        })
        assert response.status_code == 400
        response = client.post("/api/v1/index/revisions", json={
            "source_id": "source", "revision_sequence": 1,
            "revision_id": "rev", "profiles": {},
        })
        assert response.status_code == 400



def _knowledge_record(source_id="rfc-1", sequence=1, revision_id="rev-1", content="the body"):
    return {
        "source_id": source_id,
        "revision_sequence": sequence,
        "revision_id": revision_id,
        "title": "RFC 1",
        "content": content,
        "provenance": {"origin": "rfc-editor", "locator": "https://example.org/rfc1"},
        "profiles": {"chunking": "window-v1@1", "tokenization": "lexical-v1@1"},
    }


def test_knowledge_store_readback_and_provenance(tmp_path):
    with _client(tmp_path) as client:
        response = client.post("/api/v1/knowledge/records", json=_knowledge_record())
        assert response.status_code == 200
        body = response.json()
        assert (body["decision"], body["stored"]) == ("accepted", True)
        record = client.get("/api/v1/knowledge/records/rfc-1").json()["record"]
        assert record["content"] == "the body"
        assert record["provenance"]["origin"] == "rfc-editor"
        assert record["provenance"]["locator"] == "https://example.org/rfc1"
        assert len(record["content_sha256"]) == 64


def test_knowledge_vector_records_follow_revision_and_tombstone():
    from ensemble.vector_memory import InMemoryVectorStore

    class Embeddings:
        available = True

        def status(self):
            return {"ok": True, "ready": True, "model": "knowledge-test", "dimensions": 3}

        async def embed(self, texts, mode="document"):
            return [[1.0, 0.0, 0.0] for _ in texts]

    vectors = InMemoryVectorStore()
    app = create_app(
        store=InMemoryDocumentStore(),
        embedding_provider=Embeddings(),
        vector_store=vectors,
    )
    with TestClient(app) as client:
        first = client.post("/api/v1/knowledge/records", json=_knowledge_record(
            source_id="vector-knowledge", content="Knowledge vector revision one."
        ))
        assert first.status_code == 200
        collection = next(item for item in client.get("/api/v1/vector/collections").json()["collections"]
                          if item["name"] == "knowledge_chunks")
        assert collection["count"] == 1

        second = client.post("/api/v1/knowledge/records", json=_knowledge_record(
            source_id="vector-knowledge", sequence=2, revision_id="rev-2",
            content="Knowledge vector revision two.",
        ))
        assert second.status_code == 200
        collection = next(item for item in client.get("/api/v1/vector/collections").json()["collections"]
                          if item["name"] == "knowledge_chunks")
        assert collection["count"] == 1

        tombstone = client.post(
            "/api/v1/knowledge/records/vector-knowledge/tombstone",
            json={"revision_sequence": 3},
        )
        assert tombstone.json()["decision"] == "tombstoned"
        collection = next(item for item in client.get("/api/v1/vector/collections").json()["collections"]
                          if item["name"] == "knowledge_chunks")
        assert collection["count"] == 0


def test_knowledge_publication_enters_hybrid_search_and_graph(tmp_path):
    with _client(tmp_path) as client:
        payload = _knowledge_record(
            source_id="rfc-entity",
            content="CPSEARCH-10177 documents the graph-aware retrieval contract.",
        )
        response = client.post("/api/v1/knowledge/records", json=payload)
        assert response.status_code == 200
        assert response.json()["document"]["source_id"] == "rfc-entity"

        result = client.post("/api/v1/search/hybrid", json={
            "query": "CPSEARCH-10177", "limit": 10,
        })
        assert result.status_code == 200
        hit = next(item for item in result.json()["results"] if item["source_id"] == "rfc-entity")
        assert hit["graph_score"] > 0
        assert result.json()["graph_expansion"]["candidate_sources"] == 1
        assert result.json()["graph_expansion"]["duration_ms"] >= 0
        assert result.json()["channels"]["lexical"]["duration_ms"] >= 0

        tombstone = client.post(
            "/api/v1/knowledge/records/rfc-entity/tombstone",
            json={"revision_sequence": 2},
        )
        assert tombstone.json()["decision"] == "tombstoned"
        after_delete = client.post("/api/v1/search/hybrid", json={
            "query": "CPSEARCH-10177", "limit": 10,
        }).json()
        assert all(item["source_id"] != "rfc-entity" for item in after_delete["results"])


def test_knowledge_is_index_gated_and_revision_ordered(tmp_path):
    with _client(tmp_path) as client:
        first = client.post("/api/v1/knowledge/records", json=_knowledge_record()).json()
        duplicate = client.post("/api/v1/knowledge/records", json=_knowledge_record()).json()
        assert first["decision"] == "accepted"
        assert duplicate["decision"] == "duplicate"
        assert duplicate["stored"] is True

        newer = client.post(
            "/api/v1/knowledge/records",
            json=_knowledge_record(sequence=2, revision_id="rev-2", content="new body"),
        ).json()
        assert newer["decision"] == "accepted"
        stale = client.post(
            "/api/v1/knowledge/records",
            json=_knowledge_record(content="stale body"),
        ).json()
        assert (stale["decision"], stale["stored"]) == ("superseded", False)
        record = client.get("/api/v1/knowledge/records/rfc-1").json()["record"]
        assert record["revision_sequence"] == 2
        assert record["content"] == "new body"


def test_knowledge_tombstone_blocks_future_revisions(tmp_path):
    with _client(tmp_path) as client:
        client.post("/api/v1/knowledge/records", json=_knowledge_record())
        tombstone = client.post(
            "/api/v1/knowledge/records/rfc-1/tombstone",
            json={"revision_sequence": 2},
        ).json()
        assert tombstone["decision"] == "tombstoned"
        record = client.get("/api/v1/knowledge/records/rfc-1").json()["record"]
        assert record["tombstoned"] is True
        later = client.post(
            "/api/v1/knowledge/records",
            json=_knowledge_record(sequence=3, revision_id="rev-3", content="resurrected"),
        ).json()
        assert (later["decision"], later["stored"]) == ("tombstoned", False)
        assert client.get("/api/v1/knowledge/records/rfc-1").json()["record"]["content"] == "the body"


def test_knowledge_records_persist_across_restart(tmp_path):
    with _client(tmp_path) as client:
        client.post("/api/v1/knowledge/records", json=_knowledge_record("persisted-rfc"))
    with _client(tmp_path) as client:
        record = client.get("/api/v1/knowledge/records/persisted-rfc").json()["record"]
        assert record["source_id"] == "persisted-rfc"
        assert record["revision_id"] == "rev-1"


def test_knowledge_validation_rejects_missing_provenance_and_profiles(tmp_path):
    with _client(tmp_path) as client:
        payload = _knowledge_record()
        payload["provenance"] = {"origin": "source"}
        assert client.post("/api/v1/knowledge/records", json=payload).status_code == 400
        payload = _knowledge_record()
        payload["profiles"] = {}
        assert client.post("/api/v1/knowledge/records", json=payload).status_code == 400
        assert client.get("/api/v1/knowledge/records/missing").status_code == 404


def test_document_ingestion_uses_versioned_markdown_profile_and_persists_provenance(tmp_path):
    payload = {
        "source_id": "markdown-source",
        "revision": 1,
        "title": "Markdown source",
        "content": "# Architecture\n\nThe retrieval pipeline preserves heading provenance.",
        "chunk_profile": "markdown-v1",
        "media_type": "text/markdown",
    }
    with _client(tmp_path) as client:
        ingested = client.post("/api/v1/ingest/documents", json=payload)
        assert ingested.status_code == 201
        assert ingested.json()["chunk_profile"] == "markdown-v1"
        hits = client.post("/api/v1/search", json={"query": "retrieval pipeline"}).json()
        assert len(hits) == 1
        assert hits[0]["chunk_profile"] == "markdown-v1"
        assert hits[0]["chunk_header"] == "Architecture"
        assert payload["content"][hits[0]["chunk_start"]:hits[0]["chunk_end"]] == hits[0]["text"]

    with _client(tmp_path) as client:
        hits = client.post("/api/v1/search", json={"query": "heading provenance"}).json()
        assert hits[0]["chunk_profile"] == "markdown-v1"
        assert hits[0]["chunk_header"] == "Architecture"


def test_document_ingestion_rejects_unknown_chunk_profile(tmp_path):
    with _client(tmp_path) as client:
        response = client.post("/api/v1/ingest/documents", json={
            "source_id": "invalid-profile",
            "revision": 1,
            "title": "Invalid profile",
            "content": "This should not be ingested.",
            "chunk_profile": "not-a-profile",
        })
        assert response.status_code == 422


class _FailingEmbeddingProvider:
    available = True

    def status(self):
        return {"ok": True, "ready": True, "model": "test-model"}

    async def embed(self, texts, mode="document"):
        raise RuntimeError("simulated embedding outage")


def test_lexical_and_hybrid_capability_routes(tmp_path):
    app = create_app(
        store=InMemoryDocumentStore(),
        embedding_provider=_FailingEmbeddingProvider(),
    )
    with TestClient(app) as client:
        ingested = client.post("/api/v1/lexical/documents", json={
            "source_id": "lexical-doc",
            "revision": 1,
            "title": "Lexical retrieval",
            "content": "Lexical retrieval must continue when embedding inference is unavailable.",
        })
        assert ingested.status_code == 201
        lexical = client.post("/api/v1/search/lexical", json={"query": "embedding inference"})
        assert lexical.status_code == 200
        assert lexical.json()["results"][0]["source_id"] == "lexical-doc"

        hybrid = client.post("/api/v1/search/hybrid", json={"query": "embedding inference"})
        assert hybrid.status_code == 200
        assert hybrid.json()["retrieval"] == "lexical"
        assert hybrid.json()["degraded"] == ["embeddings"]
        assert hybrid.json()["results"][0]["source_id"] == "lexical-doc"


def test_hybrid_prompt_capture_is_idempotent(tmp_path):
    app = create_app(store=InMemoryDocumentStore())
    payload = {
        "session_id": "session-1",
        "prompt": "Find the prior decision about lexical indexing.",
        "timestamp": "2026-11-02T12:00:00Z",
        "cwd": "/home/test/project",
    }
    with TestClient(app) as client:
        first = client.post("/api/v1/memory/events", json=payload)
        second = client.post("/api/v1/memory/events", json=payload)
        assert first.status_code == 200
        assert first.json()["status"] == "stored"
        assert second.json()["status"] == "duplicate"
        assert first.json()["event_id"] == second.json()["event_id"]
        assert first.json()["vector_indexed"] is False
        assert first.json()["lexical_indexed"] is False


def test_sqlite_lexical_index_uses_shared_analyzer_and_porter_stemming(tmp_path):
    with _client(tmp_path) as client:
        ingested = client.post("/api/v1/ingest/documents", json={
            "source_id": "stemmed-source",
            "revision": 1,
            "title": "Runtime behavior",
            "content": "The service keeps running overnight and memory-service remains available.",
        })
        assert ingested.status_code == 201
        stemmed = client.post("/api/v1/search/lexical", json={"query": "run", "limit": 10})
        assert stemmed.status_code == 200
        assert stemmed.json()["results"][0]["source_id"] == "stemmed-source"
        compound = client.post("/api/v1/search/lexical", json={"query": "memory-service", "limit": 10})
        assert compound.status_code == 200
        assert compound.json()["results"][0]["source_id"] == "stemmed-source"


def test_sqlite_lexical_index_replaces_reingested_revision(tmp_path):
    with _client(tmp_path) as client:
        payload = {
            "source_id": "fts-replace",
            "revision": 1,
            "title": "FTS replacement",
            "content": "obsolete terminology lives here",
        }
        assert client.post("/api/v1/ingest/documents", json=payload).status_code == 201
        replacement = {**payload, "revision": 2, "content": "current terminology lives here"}
        assert client.post("/api/v1/ingest/documents", json=replacement).status_code == 201
        old = client.post("/api/v1/search/lexical", json={"query": "obsolete"}).json()
        current = client.post("/api/v1/search/lexical", json={"query": "current"}).json()
        assert old["results"] == []
        assert current["results"][0]["source_id"] == "fts-replace"


class _DeterministicReranker:
    available = True

    def status(self):
        return {"ok": True, "ready": True, "model": "test-cross-encoder"}

    def score(self, query, documents):
        return [0.95 if "preferred" in text else 0.05 for text in documents]


def test_hybrid_search_can_apply_opt_in_cross_encoder_reranking():
    app = create_app(
        store=InMemoryDocumentStore(),
        reranker=_DeterministicReranker(),
    )
    with TestClient(app) as client:
        for source_id, content in [
            ("first", "topic ordinary candidate"),
            ("preferred", "topic preferred candidate"),
        ]:
            response = client.post("/api/v1/ingest/documents", json={
                "source_id": source_id,
                "revision": 1,
                "title": source_id,
                "content": content,
            })
            assert response.status_code == 201

        result = client.post("/api/v1/search/hybrid", json={
            "query": "topic", "limit": 10, "rerank": True,
        })
        assert result.status_code == 200
        body = result.json()
        assert body["reranked"] is True
        assert body["results"][0]["source_id"] == "preferred"
        assert body["results"][0]["rerank_score"] == 0.95


def test_hybrid_search_enriches_intent_and_related_graph_sessions():
    graph = InMemoryGraphStore()
    async def seed_graph():
        session = await graph.add_node("session", {
            "session_id": "session-42", "project": "ensemble",
        })
        file_node = await graph.add_node("file", {
            "path": "/work/ensemble/ensemble/api.py",
        })
        await graph.add_edge(session["id"], file_node["id"], "touched", {})
    import asyncio
    asyncio.run(seed_graph())
    app = create_app(store=InMemoryDocumentStore(), graph_store=graph)
    with TestClient(app) as client:
        result = client.post("/api/v1/search/hybrid", json={
            "query": "why does /work/ensemble/ensemble/api.py fail",
        })
        assert result.status_code == 200
        body = result.json()
        assert body["intent"]["label"] == "debug"
        assert body["related_sessions"][0]["session_id"] == "session-42"
        assert body["related_sessions"][0]["project"] == "ensemble"


def test_hybrid_raw_jsonl_transcript_ingestion_returns_byte_checkpoint_and_is_idempotent():
    app = create_app(store=InMemoryDocumentStore())
    complete = json.dumps({
        "sessionId": "raw-jsonl-session",
        "timestamp": "2026-11-02T10:00:00Z",
        "uuid": "raw-message-1",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "Please ingest this résumé with Unicode safely."}],
        },
    }, ensure_ascii=False) + "\n"
    partial = '{"uuid":"unfinished","message":'
    payload = {
        "session_id": "fallback-session",
        "project": "ensemble",
        "file": "caller-supplied/session.jsonl",
        "offset": 4096,
        "content": complete + partial,
    }
    with TestClient(app) as client:
        first = client.post("/api/v1/ingest/transcripts", json=payload)
        assert first.status_code == 200
        body = first.json()
        assert body["session_id"] == "raw-jsonl-session"
        assert body["inserted"] == 1
        assert body["bytes_consumed"] == len(complete.encode("utf-8"))
        assert body["next_offset"] == 4096 + len(complete.encode("utf-8"))
        assert body["incomplete_tail"] is True

        retry = client.post("/api/v1/ingest/transcripts", json=payload)
        assert retry.status_code == 200
        assert retry.json()["inserted"] == 0
        assert retry.json()["already_present"] == 1


def test_pdf_ingestion_rejects_non_extractable_pdf_with_clear_error():
    app = create_app(store=InMemoryDocumentStore())
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/ingest/files",
            params={"source_id": "upload:test.pdf", "title": "Test PDF"},
            content=b"%PDF-not-a-valid-document",
            headers={"content-type": "application/pdf"},
        )
        assert response.status_code == 422
        assert "could not be parsed" in response.json()["detail"]


def test_hybrid_session_ingestion_is_idempotent_and_populates_retrieval_graph():
    graph = InMemoryGraphStore()
    app = create_app(store=InMemoryDocumentStore(), graph_store=graph)
    payload = {
        "session": {
            "session_id": "session-100",
            "project": "ensemble",
            "file": "/work/ensemble/session-100.jsonl",
            "title": "Ensemble debugging session",
            "is_subagent": True,
        },
        "messages": [
            {
                "uuid": "message-1", "role": "user", "kind": "text",
                "text": "Please inspect /work/ensemble/config.py for the retry behavior.",
                "ts": "2026-11-02T10:00:00Z",
            },
            {
                "uuid": "message-2", "role": "assistant", "kind": "text",
                "text": "The retry setting in /work/ensemble/config.py is bounded.",
                "ts": "2026-11-02T10:00:10Z",
            },
        ],
    }
    with TestClient(app) as client:
        first = client.post("/api/v1/ingest/sessions", json=payload)
        second = client.post("/api/v1/ingest/sessions", json=payload)
        assert first.status_code == 200
        assert first.json()["inserted"] == 2
        assert second.status_code == 200
        assert second.json()["already_present"] == 2

        search = client.post("/api/v1/search/hybrid", json={
            "query": "why does /work/ensemble/config.py fail",
        })
        assert search.status_code == 200
        assert search.json()["results"]
        assert search.json()["related_sessions"][0]["session_id"] == "session-100"
        assert search.json()["results"][0]["session_id"] == "session-100"
        assert search.json()["results"][0]["project"] == "ensemble"
        assert search.json()["results"][0]["is_subagent"] is True


def test_transcript_message_content_change_replaces_active_revision():
    app = create_app(store=InMemoryDocumentStore())
    base = {
        "session": {
            "session_id": "session-revision",
            "project": "ensemble",
            "file": "/work/session-revision.jsonl",
        },
        "messages": [{
            "uuid": "stable-message-uuid",
            "role": "assistant",
            "kind": "text",
            "text": "The original transcript wording describes the old behavior.",
        }],
    }
    with TestClient(app) as client:
        first = client.post("/api/v1/ingest/sessions", json=base)
        assert first.status_code == 200
        assert first.json()["inserted"] == 1

        updated = {
            **base,
            "messages": [{
                **base["messages"][0],
                "text": "The revised transcript wording describes the current behavior.",
            }],
        }
        second = client.post("/api/v1/ingest/sessions", json=updated)
        assert second.status_code == 200
        assert second.json()["inserted"] == 1

        old = client.post("/api/v1/search", json={"query": "original"})
        current = client.post("/api/v1/search", json={"query": "revised transcript wording"})
        assert old.status_code == 200
        assert old.json() == []
        assert len(current.json()) == 1
        assert current.json()[0]["revision"] == 2
        assert "revised transcript wording" in current.json()[0]["text"]


def test_hybrid_session_ingestion_rejects_invalid_messages_before_writing():
    app = create_app(store=InMemoryDocumentStore())
    with TestClient(app) as client:
        response = client.post("/api/v1/ingest/sessions", json={
            "session": {
                "session_id": "session-invalid",
                "project": "ensemble",
                "file": "/work/session.jsonl",
            },
            "messages": [{"uuid": "bad", "role": "system", "text": "not accepted"}],
        })
        assert response.status_code == 400


class _FixedEmbeddingProvider:
    available = True

    def status(self):
        return {"ok": True, "ready": True, "model": "test-embedding", "dimensions": 3}

    async def embed(self, texts, mode="document"):
        return [[1.0, 0.0, 0.0] for _ in texts]


def test_hybrid_session_ingestion_populates_vector_collection_and_searches_it():
    vectors = InMemoryVectorStore()
    app = create_app(
        store=InMemoryDocumentStore(),
        embedding_provider=_FixedEmbeddingProvider(),
        vector_store=vectors,
    )
    payload = {
        "session": {
            "session_id": "vector-session",
            "project": "ensemble",
            "file": "/work/ensemble/vector-session.jsonl",
        },
        "messages": [{
            "uuid": "vector-message", "role": "user", "kind": "text",
            "text": "vectorized transcript message for hybrid search",
        }],
    }
    with TestClient(app) as client:
        ingested = client.post("/api/v1/ingest/sessions", json=payload)
        assert ingested.status_code == 200
        assert ingested.json()["inserted"] == 1

        # Timestamp changes must not create a second event or vector document:
        # content identity is based on session, message UUID, kind and text.
        retry_payload = {
            **payload,
            "messages": [{**payload["messages"][0], "ts": "2026-11-02T11:00:00Z"}],
        }
        retried = client.post("/api/v1/ingest/sessions", json=retry_payload)
        assert retried.status_code == 200
        assert retried.json()["inserted"] == 0
        assert retried.json()["already_present"] == 1

        collections = client.get("/api/v1/vector/collections").json()["collections"]
        transcript = next(item for item in collections if item["name"] == "transcript_text")
        assert transcript["count"] == 1

        result = client.post("/api/v1/search/hybrid", json={
            "query": "hybrid search", "limit": 10,
        })
        assert result.status_code == 200
        assert result.json()["retrieval"] == "hybrid"
        assert result.json()["results"][0]["source_id"].startswith("transcript:")
        assert len(result.json()["results"][0]["source_id"]) == len("transcript:") + 64


def test_lexical_documents_accepts_poc_session_message_contract():
    app = create_app(store=InMemoryDocumentStore())
    with TestClient(app) as client:
        response = client.post("/api/v1/lexical/documents", json={
            "session": {
                "session_id": "lexical-session",
                "project": "ensemble",
                "file": "/work/ensemble/lexical-session.jsonl",
            },
            "messages": [{
                "uuid": "lexical-message", "role": "assistant", "kind": "text",
                "text": "Porter stemming keeps lexical session ingestion searchable.",
            }],
        })
        assert response.status_code == 200
        assert response.json()["inserted"] == 1
        results = client.post("/api/v1/search/lexical", json={
            "query": "searchable",
        }).json()["results"]
        assert results[0]["source_id"].startswith("transcript:")
        assert len(results[0]["source_id"]) == len("transcript:") + 64
        assert len(results[0]["text_sha"]) == 64


def test_transcript_metadata_survives_sqlite_restart(tmp_path):
    payload = {
        "session": {
            "session_id": "durable-session",
            "project": "durable-project",
            "file": "/work/durable/session.jsonl",
            "is_subagent": True,
        },
        "messages": [{
            "uuid": "durable-message", "role": "assistant", "kind": "tool_result",
            "text": "The durable transcript metadata remains attached to the tool result.",
        }],
    }
    with _client(tmp_path) as client:
        response = client.post("/api/v1/ingest/sessions", json=payload)
        assert response.status_code == 200
        assert response.json()["inserted"] == 1

    with _client(tmp_path) as client:
        results = client.post("/api/v1/search/lexical", json={
            "query": "durable transcript metadata",
        }).json()["results"]
        assert results
        assert results[0]["session_id"] == "durable-session"
        assert results[0]["project"] == "durable-project"
        assert results[0]["role"] == "assistant"
        assert results[0]["kind"] == "tool_result"
        assert results[0]["is_subagent"] is True


def test_lexical_search_suppresses_identical_message_text_across_sessions():
    app = create_app(store=InMemoryDocumentStore())
    message_text = "The same pasted stack trace appears in both sessions."
    with TestClient(app) as client:
        for session_id in ("session-a", "session-b"):
            response = client.post("/api/v1/lexical/documents", json={
                "session": {
                    "session_id": session_id,
                    "project": "ensemble",
                    "file": f"/work/{session_id}.jsonl",
                },
                "messages": [{
                    "uuid": "same-message", "role": "user", "kind": "text",
                    "text": message_text,
                }],
            })
            assert response.status_code == 200
        results = client.post("/api/v1/search/lexical", json={
            "query": "pasted stack trace", "limit": 10,
        }).json()["results"]
        assert len(results) == 1
        assert results[0]["text"] == message_text


def test_successful_hybrid_search_is_recorded_in_memory_search_ledger():
    app = create_app(store=InMemoryDocumentStore())
    with TestClient(app) as client:
        client.post("/api/v1/ingest/documents", json={
            "source_id": "search-ledger-doc", "revision": 1,
            "title": "Search ledger", "content": "The retrieval pipeline is observable.",
        })
        response = client.post("/api/v1/search/hybrid", json={"query": "retrieval pipeline"})
        assert response.status_code == 200
        entries = client.get("/api/v1/memory/memories/search-ledger/entries").json()["entries"]
        assert len(entries) == 1
        assert entries[0]["query"] == "retrieval pipeline"
        assert entries[0]["result_count"] == 1


def test_search_ledger_failure_does_not_fail_successful_hybrid_search():
    class FailingSearchLedger(InMemoryMemoryStore):
        async def append(self, name, payload, event_id=None):
            if name == "search-ledger":
                raise OSError("simulated ledger failure")
            return await super().append(name, payload, event_id)

    app = create_app(
        store=InMemoryDocumentStore(),
        memory_store=FailingSearchLedger(),
    )
    with TestClient(app) as client:
        client.post("/api/v1/ingest/documents", json={
            "source_id": "search-ledger-failure", "revision": 1,
            "title": "Search ledger", "content": "Retrieval remains available.",
        })
        response = client.post("/api/v1/search/hybrid", json={"query": "retrieval"})
        assert response.status_code == 200
        assert response.json()["results"]


class _BatchCountingEmbeddingProvider:
    available = True

    def __init__(self):
        self.batch_sizes = []

    def status(self):
        return {"ok": True, "ready": True, "model": "batch-test", "dimensions": 3}

    async def embed(self, texts, mode="document"):
        self.batch_sizes.append(len(texts))
        assert len(texts) <= 64
        return [[1.0, 0.0, 0.0] for _ in texts]


def test_long_transcript_messages_are_fully_chunked_and_embedding_batches_are_bounded():
    provider = _BatchCountingEmbeddingProvider()
    vectors = InMemoryVectorStore()
    app = create_app(
        store=InMemoryDocumentStore(),
        embedding_provider=provider,
        vector_store=vectors,
    )
    message_text = ("ordinary transcript words " * 5000) + "tailmarkerunique"
    with TestClient(app) as client:
        response = client.post("/api/v1/ingest/sessions", json={
            "session": {
                "session_id": "long-session",
                "project": "ensemble",
                "file": "/work/long-session.jsonl",
            },
            "messages": [{
                "uuid": "long-message", "role": "assistant", "kind": "text",
                "text": message_text,
            }],
        })
        assert response.status_code == 200
        assert len(provider.batch_sizes) > 1
        assert max(provider.batch_sizes) <= 64
        collections = client.get("/api/v1/vector/collections").json()["collections"]
        transcript = next(item for item in collections if item["name"] == "transcript_text")
        assert transcript["count"] > 64
        results = client.post("/api/v1/search/lexical", json={
            "query": "tailmarkerunique", "limit": 10,
        }).json()["results"]
        assert results
        assert results[0]["chunk_profile"] == "window-v1"
        assert "tailmarkerunique" in results[0]["text"]

def test_ingestion_routes_use_only_the_canonical_namespace():
    app = create_app(store=InMemoryDocumentStore())
    paths = app.openapi()["paths"]

    expected = {
        "/api/v1/ingest/documents",
        "/api/v1/ingest/files",
        "/api/v1/ingest/urls",
        "/api/v1/ingest/urls/preview",
        "/api/v1/ingest/transcripts",
        "/api/v1/ingest/sessions",
    }
    obsolete = {
        "/api/v1/documents",
        "/api/v1/ingestion/pdf",
        "/api/v1/scrape/preview",
        "/api/v1/scrape/ingest",
        "/api/v1/lexical/search",
        "/api/v1/hybrid/transcripts/ingest",
    }

    assert expected <= paths.keys()
    assert obsolete.isdisjoint(paths)


def test_sqlite_index_generation_registry_requires_sealed_checkpoints_and_verified_cutover(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'generation.db'}"
    migration_config = Config("alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")
    store = SqliteIndexStore(database_url)

    async def exercise():
        await store.initialize()
        initial = await store.generation_status()
        assert initial["active_generation_id"] == "generation-0001"
        assert initial["epoch"] == 1

        await store.record_revision("source-prebuild", 1, "revision-prebuild", {"chunking": "window-v1"})
        await store.create_generation("generation-0002")
        accepted = await store.record_revision("source-a", 1, "revision-a", {"chunking": "window-v1"})
        assert accepted["decision"] == "accepted"
        tombstoned = await store.record_tombstone("source-a", 2)
        assert tombstoned["decision"] == "tombstoned"
        journal = await store.list_generation_changes_after("generation-0002", 0, 10)
        assert [item["tombstoned"] for item in journal] == [False, False, True]
        high_watermark = await store.generation_change_high_watermark("generation-0002")
        with pytest.raises(RuntimeError, match="unacknowledged source mutations"):
            await store.acquire_generation_write_fence("generation-0002", through_change_id=high_watermark)
        assert await store.acknowledge_generation_change("source-prebuild", 1, tombstoned=False) == 1
        assert await store.acknowledge_generation_change("source-a", 1, tombstoned=False) == 1
        assert await store.acknowledge_generation_change("source-a", 2, tombstoned=True) == 1
        with pytest.raises(RuntimeError, match="must all be sealed"):
            await store.activate_generation(
                "generation-0002", "generation-0001", 1,
                {"acknowledged_writes_verified": True, "tombstones_verified": True},
            )

        for capability in ("index", "documents", "vectors", "memory", "knowledge"):
            await store.record_generation_checkpoint(
                "generation-0002", capability,
                {"cursor": capability + "-complete"}, sealed=True,
            )
        with pytest.raises(RuntimeError, match="already sealed"):
            await store.record_generation_checkpoint(
                "generation-0002", "index", {"cursor": "changed"}, sealed=False,
            )
        with pytest.raises(ValueError, match="acknowledged-write"):
            await store.activate_generation(
                "generation-0002", "generation-0001", 1,
                {"acknowledged_writes_verified": True, "tombstones_verified": False},
            )
        await store.record_generation_checkpoint(
            "generation-0002", "index_reconciliation", {"cursor": high_watermark}, sealed=True,
        )
        for capability in ("documents_reconciliation", "vectors_reconciliation", "memory_reconciliation", "knowledge_reconciliation"):
            await store.record_generation_checkpoint(
                "generation-0002", capability, {"cursor": high_watermark}, sealed=True,
            )
        await store.acquire_generation_write_fence("generation-0002", through_change_id=high_watermark)
        fenced = await store.record_revision("source-b", 1, "revision-b", {"chunking": "window-v1"})
        assert fenced["decision"] == "fenced"

        cutover = await store.activate_generation(
            "generation-0002", "generation-0001", 1,
            {"acknowledged_writes_verified": True, "tombstones_verified": True,
             "reconciled_through_change_id": high_watermark},
        )
        assert cutover == {"active_generation_id": "generation-0002", "epoch": 2}
        status = await store.generation_status()
        states = {item["generation_id"]: item["state"] for item in status["generations"]}
        assert states == {"generation-0001": "retired", "generation-0002": "active"}
        with pytest.raises(RuntimeError, match="active generation changed"):
            await store.activate_generation(
                "generation-0002", "generation-0001", 1,
                {"acknowledged_writes_verified": True, "tombstones_verified": True},
            )
        await store.close()

    asyncio.run(exercise())


def test_in_memory_generation_registry_enforces_single_build_and_cutover():
    store = InMemoryIndexStore()

    async def exercise():
        status = await store.generation_status()
        assert status["active_generation_id"] == "generation-0001"
        await store.create_generation("generation-0002")
        with pytest.raises(RuntimeError, match="already in progress"):
            await store.create_generation("generation-0003")
        assert (await store.record_revision("source-a", 1, "revision-a", {"chunking": "window-v1"}))["decision"] == "accepted"
        assert (await store.record_tombstone("source-a", 2))["decision"] == "tombstoned"
        journal = await store.list_generation_changes_after("generation-0002", 0, 10)
        assert [item["tombstoned"] for item in journal] == [False, True]
        assert await store.acknowledge_generation_change("source-a", 1, tombstoned=False) == 1
        assert await store.acknowledge_generation_change("source-a", 2, tombstoned=True) == 1
        high_watermark = await store.generation_change_high_watermark("generation-0002")
        for capability in ("index", "documents", "vectors", "memory", "knowledge",
                           "index_reconciliation", "documents_reconciliation", "vectors_reconciliation",
                           "memory_reconciliation", "knowledge_reconciliation"):
            await store.record_generation_checkpoint(
                "generation-0002", capability,
                {"cursor": "done" if not capability.endswith("_reconciliation") else high_watermark},
                sealed=True,
            )
        await store.acquire_generation_write_fence("generation-0002", through_change_id=high_watermark)
        assert (await store.record_revision("source-b", 1, "revision-b", {"chunking": "window-v1"}))["decision"] == "fenced"
        result = await store.activate_generation(
            "generation-0002", "generation-0001", 1,
            {"acknowledged_writes_verified": True, "tombstones_verified": True,
             "reconciled_through_change_id": high_watermark},
        )
        assert result["active_generation_id"] == "generation-0002"
        assert result["epoch"] == 2

    asyncio.run(exercise())


def test_vector_generation_storage_isolated_from_other_generations(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'vector-generations.db'}"
    migration_config = Config("alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")
    index_store = SqliteIndexStore(database_url)
    vector_store = SqliteVectorStore(database_url)

    async def exercise():
        await index_store.initialize()
        await vector_store.initialize()
        await index_store.create_generation("generation-shadow")
        first = [{
            "id": "same-record",
            "embedding": [1.0, 0.0],
            "document": "active generation content",
            "metadata": {"revision": 1},
        }]
        shadow = [{
            "id": "same-record",
            "embedding": [0.0, 1.0],
            "document": "shadow generation content",
            "metadata": {"revision": 2},
        }]
        await vector_store.upsert("knowledge", "test-model", 2, first)
        await vector_store.upsert_generation(
            "generation-shadow", "knowledge", "test-model", 2, shadow,
        )

        active_results = await vector_store.query("knowledge", [1.0, 0.0], 5)
        shadow_results = await vector_store.query_generation(
            "generation-shadow", "knowledge", [0.0, 1.0], 5,
        )
        assert [item["document"] for item in active_results] == ["active generation content"]
        assert [item["document"] for item in shadow_results] == ["shadow generation content"]
        assert await vector_store.delete_generation("generation-shadow") == 1
        assert await vector_store.query_generation(
            "generation-shadow", "knowledge", [0.0, 1.0], 5,
        ) == []
        assert [item["document"] for item in await vector_store.query(
            "knowledge", [1.0, 0.0], 5,
        )] == ["active generation content"]
        await vector_store.close()
        await index_store.close()

    asyncio.run(exercise())


def test_in_memory_vector_generation_storage_isolated():
    store = InMemoryVectorStore()

    async def exercise():
        await store.upsert("knowledge", "test-model", 2, [{
            "id": "shared", "embedding": [1.0, 0.0],
            "document": "active", "metadata": {},
        }])
        await store.upsert_generation("shadow", "knowledge", "test-model", 2, [{
            "id": "shared", "embedding": [0.0, 1.0],
            "document": "shadow", "metadata": {},
        }])
        assert (await store.query("knowledge", [1.0, 0.0], 5))[0]["document"] == "active"
        assert (await store.query_generation("shadow", "knowledge", [0.0, 1.0], 5))[0]["document"] == "shadow"
        assert await store.delete_generation("shadow") == 1
        assert await store.query_generation("shadow", "knowledge", [0.0, 1.0], 5) == []

    asyncio.run(exercise())


def test_document_generation_storage_isolated_and_revision_aware(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'document-generations.db'}"
    migration_config = Config("alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")
    store = SqlDocumentStore(database_url)
    index_store = SqliteIndexStore(database_url)

    async def exercise():
        await store.initialize()
        await index_store.initialize()
        await index_store.create_generation("generation-shadow")

        active = Document(
            "same-source", 1, "Active document",
            "The active generation contains original lexical terminology.",
        )
        active_chunk = Chunk(active.id, 0, active.content, [1.0, 0.0])
        await store.save(active, [active_chunk])

        shadow_v1 = Document(
            "same-source", 1, "Shadow document",
            "The shadow generation contains replacement terminology.",
        )
        shadow_v1_chunk = Chunk(shadow_v1.id, 0, shadow_v1.content, [0.0, 1.0])
        await store.upsert_generation("generation-shadow", shadow_v1, [shadow_v1_chunk])

        shadow_v2 = Document(
            "same-source", 2, "Shadow document revision two",
            "The newest shadow generation contains final lexical terminology.",
        )
        shadow_v2_chunk = Chunk(shadow_v2.id, 0, shadow_v2.content, [0.5, 0.5])
        await store.upsert_generation("generation-shadow", shadow_v2, [shadow_v2_chunk])

        active_chunks = await store.all_chunks()
        shadow_chunks = await store.generation_all_chunks("generation-shadow")
        assert len(active_chunks) == 1
        assert active_chunks[0].chunk.content == active.content
        assert len(shadow_chunks) == 1
        assert shadow_chunks[0].document.revision == 2
        assert shadow_chunks[0].chunk.content == shadow_v2.content

        hits = await store.generation_lexical_search(
            "generation-shadow", "final lexical terminology", 10,
        )
        assert len(hits) == 1
        assert hits[0][0].document.revision == 2

        assert await store.delete_generation("generation-shadow") == 2
        assert await store.generation_all_chunks("generation-shadow") == []
        assert len(await store.all_chunks()) == 1
        await store.close()
        await index_store.close()

    asyncio.run(exercise())


def test_in_memory_document_generation_storage_isolated():
    store = InMemoryDocumentStore()

    async def exercise():
        active = Document("same-source", 1, "Active", "active source content")
        await store.save(active, [Chunk(active.id, 0, active.content)])
        shadow = Document("same-source", 1, "Shadow", "shadow source content")
        await store.upsert_generation(
            "shadow", shadow, [Chunk(shadow.id, 0, shadow.content)],
        )
        assert (await store.all_chunks())[0].chunk.content == "active source content"
        assert (await store.generation_all_chunks("shadow"))[0].chunk.content == "shadow source content"
        assert await store.delete_generation("shadow") == 1
        assert await store.generation_all_chunks("shadow") == []

    asyncio.run(exercise())


def test_shadow_generation_tombstones_prevent_document_resurrection_and_remove_vectors(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'generation-tombstones.db'}"
    migration_config = Config("alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")
    index_store = SqliteIndexStore(database_url)
    document_store = SqlDocumentStore(database_url)
    vector_store = SqliteVectorStore(database_url)

    async def exercise():
        await index_store.initialize()
        await document_store.initialize()
        await vector_store.initialize()
        await index_store.create_generation("generation-shadow")
        document = Document("tombstoned-source", 1, "Shadow source", "source content to delete")
        chunk = Chunk(document.id, 0, document.content, [1.0, 0.0])
        await document_store.upsert_generation("generation-shadow", document, [chunk])
        await vector_store.upsert_generation(
            "generation-shadow", "knowledge", "test-model", 2, [{
                "id": "shadow-chunk",
                "embedding": [1.0, 0.0],
                "document": document.content,
                "metadata": {"source_id": "tombstoned-source"},
            }],
        )

        assert await document_store.tombstone_generation_source(
            "generation-shadow", "tombstoned-source",
        ) == 1
        assert await document_store.tombstone_generation_source(
            "generation-shadow", "tombstoned-source",
        ) == 0
        assert await document_store.generation_all_chunks("generation-shadow") == []
        with pytest.raises(ValueError, match="tombstoned"):
            await document_store.upsert_generation(
                "generation-shadow", document, [chunk],
            )
        assert await vector_store.tombstone_generation_source(
            "generation-shadow", "tombstoned-source",
        ) == 1
        assert await vector_store.query_generation(
            "generation-shadow", "knowledge", [1.0, 0.0], 5,
        ) == []
        await vector_store.close()
        await document_store.close()
        await index_store.close()

    asyncio.run(exercise())


def test_in_memory_shadow_generation_tombstones_are_idempotent():
    documents = InMemoryDocumentStore()
    vectors = InMemoryVectorStore()

    async def exercise():
        document = Document("deleted", 1, "Deleted", "deleted content")
        await documents.upsert_generation(
            "shadow", document, [Chunk(document.id, 0, document.content)],
        )
        await vectors.upsert_generation("shadow", "knowledge", "model", 2, [{
            "id": "deleted-chunk", "embedding": [1.0, 0.0],
            "document": "deleted content", "metadata": {"source_id": "deleted"},
        }])
        assert await documents.tombstone_generation_source("shadow", "deleted") == 1
        assert await documents.tombstone_generation_source("shadow", "deleted") == 0
        assert await documents.generation_all_chunks("shadow") == []
        with pytest.raises(ValueError, match="tombstoned"):
            await documents.upsert_generation(
                "shadow", document, [Chunk(document.id, 0, document.content)],
            )
        assert await vectors.tombstone_generation_source("shadow", "deleted") == 1
        assert await vectors.query_generation("shadow", "knowledge", [1.0, 0.0], 5) == []

    asyncio.run(exercise())


def test_index_source_keyset_pagination_is_ordered_and_bounded(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'source-pages.db'}"
    migration_config = Config("alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")
    store = SqliteIndexStore(database_url)

    async def exercise():
        await store.initialize()
        for source_id in ("source-a", "source-b", "source-c", "source-d", "source-e"):
            await store.record_revision(source_id, 1, "r1", {"chunking": "window-v1"})
        upper_bound = await store.source_key_upper_bound()
        assert upper_bound == "source-e"

        first = await store.list_sources_after(None, upper_bound, 2)
        second = await store.list_sources_after(first[-1]["source_id"], upper_bound, 2)
        third = await store.list_sources_after(second[-1]["source_id"], upper_bound, 2)
        assert [item["source_id"] for item in first] == ["source-a", "source-b"]
        assert [item["source_id"] for item in second] == ["source-c", "source-d"]
        assert [item["source_id"] for item in third] == ["source-e"]
        assert await store.list_sources_after("source-e", upper_bound, 2) == []

        await store.record_revision("source-z", 1, "r1", {"chunking": "window-v1"})
        assert await store.list_sources_after("source-e", upper_bound, 10) == []
        await store.close()

    asyncio.run(exercise())


def test_in_memory_index_source_keyset_pagination():
    store = InMemoryIndexStore()

    async def exercise():
        for source_id in ("source-a", "source-b", "source-c", "source-d"):
            await store.record_revision(source_id, 1, "r1", {})
        upper_bound = await store.source_key_upper_bound()
        first = await store.list_sources_after(None, upper_bound, 2)
        second = await store.list_sources_after(first[-1]["source_id"], upper_bound, 2)
        assert [item["source_id"] for item in first + second] == [
            "source-a", "source-b", "source-c", "source-d",
        ]
        assert await store.list_sources_after("source-d", upper_bound, 2) == []

    asyncio.run(exercise())


def test_generation_index_source_state_is_durable_monotonic_and_pageable(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'generation-index-sources.db'}"
    migration_config = Config("alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")
    store = SqliteIndexStore(database_url)

    async def exercise():
        await store.initialize()
        await store.create_generation("generation-shadow")
        for source_id in ("source-a", "source-b", "source-c"):
            result = await store.put_generation_source("generation-shadow", {
                "source_id": source_id, "high_sequence": 1,
                "revision_id": "r1", "profiles": {"chunking": "window-v1"},
                "tombstoned": False,
            })
            assert result["decision"] == "accepted"

        updated = await store.put_generation_source("generation-shadow", {
            "source_id": "source-b", "high_sequence": 2,
            "revision_id": "r2", "profiles": {"chunking": "markdown-v1"},
            "tombstoned": False,
        })
        assert updated["decision"] == "accepted"
        stale = await store.put_generation_source("generation-shadow", {
            "source_id": "source-b", "high_sequence": 1,
            "revision_id": "r1", "profiles": {"chunking": "window-v1"},
            "tombstoned": False,
        })
        assert stale["decision"] == "superseded"

        tombstone = await store.put_generation_source("generation-shadow", {
            "source_id": "source-c", "high_sequence": 3,
            "revision_id": "tombstone", "profiles": {},
            "tombstoned": True,
        })
        assert tombstone["decision"] == "tombstoned"
        delayed = await store.put_generation_source("generation-shadow", {
            "source_id": "source-c", "high_sequence": 4,
            "revision_id": "r4", "profiles": {},
            "tombstoned": False,
        })
        assert delayed["decision"] == "tombstoned"

        page1 = await store.list_generation_sources_after(
            "generation-shadow", None, "source-c", 2,
        )
        page2 = await store.list_generation_sources_after(
            "generation-shadow", page1[-1]["source_id"], "source-c", 2,
        )
        assert [item["source_id"] for item in page1 + page2] == [
            "source-a", "source-b", "source-c",
        ]
        assert (await store.get_generation_source(
            "generation-shadow", "source-b",
        ))["revision_id"] == "r2"
        assert (await store.get_generation_source(
            "generation-shadow", "source-c",
        ))["tombstoned"] is True
        await store.close()

    asyncio.run(exercise())


def test_in_memory_generation_index_source_state_matches_durable_rules():
    store = InMemoryIndexStore()

    async def exercise():
        await store.create_generation("shadow")
        source = {
            "source_id": "source-a", "high_sequence": 1,
            "revision_id": "r1", "profiles": {}, "tombstoned": False,
        }
        assert (await store.put_generation_source("shadow", source))["decision"] == "accepted"
        newer = {**source, "high_sequence": 2, "revision_id": "r2"}
        assert (await store.put_generation_source("shadow", newer))["decision"] == "accepted"
        assert (await store.put_generation_source("shadow", source))["decision"] == "superseded"
        tombstone = {**newer, "high_sequence": 3, "revision_id": "tombstone", "tombstoned": True}
        assert (await store.put_generation_source("shadow", tombstone))["decision"] == "tombstoned"
        assert (await store.put_generation_source("shadow", newer))["decision"] == "tombstoned"
        assert (await store.list_generation_sources_after("shadow", None, "source-a", 10))[0]["tombstoned"] is True

    asyncio.run(exercise())


def test_index_generation_builder_copies_source_pages_and_resumes(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'generation-builder.db'}"
    migration_config = Config("alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")
    store = SqliteIndexStore(database_url)

    async def exercise():
        await store.initialize()
        await store.record_revision("source-a", 1, "r1", {"chunking": "window-v1"})
        await store.record_revision("source-b", 1, "r1", {"chunking": "window-v1"})
        await store.record_tombstone("source-c", 2)
        await store.create_generation("generation-shadow")

        first_builder = IndexGenerationBuilder(store)
        first = await first_builder.copy_index_sources(
            "generation-shadow", page_size=2,
        )
        assert first["status"] == "in_progress"
        assert first["page_count"] == 2
        assert first["source_count"] == 2

        # A key beyond the captured upper bound belongs to the next rebuild.
        await store.record_revision("source-z", 1, "r1", {})
        resumed_builder = IndexGenerationBuilder(store)
        second = await resumed_builder.copy_index_sources(
            "generation-shadow", page_size=2,
        )
        assert second["status"] == "complete"
        assert second["source_count"] == 3
        assert second["tombstone_count"] == 1
        assert second["upper_bound"] == "source-c"

        copied = await store.list_generation_sources_after(
            "generation-shadow", None, "source-c", 10,
        )
        assert [item["source_id"] for item in copied] == [
            "source-a", "source-b", "source-c",
        ]
        assert copied[-1]["tombstoned"] is True

        completed = await resumed_builder.copy_index_sources(
            "generation-shadow", page_size=2,
        )
        assert completed["status"] == "complete"
        assert completed["page_count"] == 0
        await store.close()

    asyncio.run(exercise())


def test_in_memory_index_generation_builder_resumes_and_seals():
    store = InMemoryIndexStore()

    async def exercise():
        for source_id in ("a", "b", "c"):
            await store.record_revision(source_id, 1, "r1", {})
        await store.create_generation("shadow")
        builder = IndexGenerationBuilder(store)
        first = await builder.copy_index_sources("shadow", page_size=2)
        assert first["status"] == "in_progress"
        second = await builder.copy_index_sources("shadow", page_size=2)
        assert second["status"] == "complete"
        assert second["source_count"] == 3
        status = await store.generation_status()
        shadow = next(item for item in status["generations"] if item["generation_id"] == "shadow")
        assert shadow["checkpoints"]["index"]["sealed"] is True

    asyncio.run(exercise())


def test_document_generation_builder_copies_documents_and_applies_tombstones(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'document-builder.db'}"
    migration_config = Config("alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")
    index_store = SqliteIndexStore(database_url)
    document_store = SqlDocumentStore(database_url)
    vector_store = SqliteVectorStore(database_url)

    async def exercise():
        await index_store.initialize()
        await document_store.initialize()
        await vector_store.initialize()
        for source_id in ("source-a", "source-b"):
            await index_store.record_revision(source_id, 1, "r1", {})
            document = Document(source_id, 1, source_id, f"{source_id} document content")
            await document_store.save(document, [Chunk(document.id, 0, document.content)])
        await index_store.record_tombstone("source-c", 2)
        await index_store.create_generation("generation-shadow")

        builder = IndexGenerationBuilder(index_store, document_store, vector_store)
        while True:
            page = await builder.copy_index_sources("generation-shadow", page_size=2)
            if page["status"] == "complete":
                break
        first = await builder.copy_documents_page("generation-shadow", page_size=2)
        assert first["status"] == "in_progress"
        second = await builder.copy_documents_page("generation-shadow", page_size=2)
        assert second["status"] == "complete"
        assert second["document_count"] == 2
        assert second["chunk_count"] == 2
        assert second["tombstone_count"] == 1

        shadow_chunks = await document_store.generation_all_chunks("generation-shadow")
        assert {item.document.source_id for item in shadow_chunks} == {"source-a", "source-b"}
        shadow_state = await index_store.list_generation_sources_after(
            "generation-shadow", None, "source-c", 10,
        )
        assert shadow_state[-1]["tombstoned"] is True
        status = await index_store.generation_status()
        generation = next(item for item in status["generations"] if item["generation_id"] == "generation-shadow")
        assert generation["checkpoints"]["documents"]["sealed"] is True
        await vector_store.close()
        await document_store.close()
        await index_store.close()

    asyncio.run(exercise())


def test_document_generation_builder_requires_sealed_index_pass():
    index_store = InMemoryIndexStore()
    document_store = InMemoryDocumentStore()
    builder = IndexGenerationBuilder(index_store, document_store)

    async def exercise():
        await index_store.create_generation("shadow")
        with pytest.raises(RuntimeError, match="Index source copy must be sealed"):
            await builder.copy_documents_page("shadow")

    asyncio.run(exercise())


def test_vector_generation_builder_copies_live_records_and_skips_tombstones(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'vector-builder.db'}"
    migration_config = Config("alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")
    index_store = SqliteIndexStore(database_url)
    document_store = SqlDocumentStore(database_url)
    vector_store = SqliteVectorStore(database_url)

    async def exercise():
        await index_store.initialize()
        await document_store.initialize()
        await vector_store.initialize()
        await index_store.record_revision("source-live", 1, "r1", {})
        await index_store.record_tombstone("source-deleted", 2)
        live_document = Document("source-live", 1, "Live", "live document")
        await document_store.save(
            live_document, [Chunk(live_document.id, 0, live_document.content, [1.0, 0.0])],
        )
        await vector_store.upsert("knowledge", "test-model", 2, [
            {
                "id": "a-deleted", "embedding": [0.0, 1.0],
                "document": "deleted vector", "metadata": {"source_id": "source-deleted"},
            },
            {
                "id": "z-live", "embedding": [1.0, 0.0],
                "document": "live vector", "metadata": {"source_id": "source-live"},
            },
        ])
        await index_store.create_generation("generation-shadow")
        builder = IndexGenerationBuilder(index_store, document_store, vector_store)
        await builder.copy_index_sources("generation-shadow", page_size=10)
        await builder.copy_documents_page("generation-shadow", page_size=10)

        first = await builder.copy_vectors_page("generation-shadow", page_size=1)
        assert first["status"] == "in_progress"
        assert first["skipped_tombstone_count"] == 1
        second = await builder.copy_vectors_page("generation-shadow", page_size=1)
        assert second["status"] == "complete"
        assert second["copied_record_count"] == 1
        assert second["skipped_tombstone_count"] == 1

        shadow = await vector_store.query_generation(
            "generation-shadow", "knowledge", [1.0, 0.0], 10,
        )
        assert [item["id"] for item in shadow] == ["z-live"]
        active = await vector_store.query("knowledge", [1.0, 0.0], 10)
        assert {item["id"] for item in active} == {"a-deleted", "z-live"}
        status = await index_store.generation_status()
        generation = next(item for item in status["generations"] if item["generation_id"] == "generation-shadow")
        assert generation["checkpoints"]["vectors"]["sealed"] is True
        await vector_store.close()
        await document_store.close()
        await index_store.close()

    asyncio.run(exercise())


def test_vector_generation_builder_rejects_unattributed_records():
    index_store = InMemoryIndexStore()
    document_store = InMemoryDocumentStore()
    vector_store = InMemoryVectorStore()
    builder = IndexGenerationBuilder(index_store, document_store, vector_store)

    async def exercise():
        await index_store.create_generation("shadow")
        await index_store.record_generation_checkpoint(
            "shadow", "index", {"source_upper_bound": None, "cursor": None}, sealed=True,
        )
        await index_store.record_generation_checkpoint(
            "shadow", "documents", {"source_upper_bound": None, "cursor": None}, sealed=True,
        )
        await vector_store.upsert("knowledge", "model", 2, [{
            "id": "unattributed", "embedding": [1.0, 0.0],
            "document": "unknown source", "metadata": {},
        }])
        with pytest.raises(RuntimeError, match="no source identity"):
            await builder.copy_vectors_page("shadow")

    asyncio.run(exercise())


def test_generation_change_reconciliation_is_resumable_and_fenced():
    store = InMemoryIndexStore()

    async def exercise():
        await store.record_revision("source-prebuild", 1, "revision-prebuild", {"chunking": "window-v1"})
        await store.create_generation("generation-shadow")
        await store.record_generation_checkpoint(
            "generation-shadow", "index", {"cursor": "snapshot-complete"}, sealed=True,
        )
        await store.record_revision("source-a", 1, "revision-a", {"chunking": "window-v1"})
        await store.record_revision("source-b", 1, "revision-b", {"chunking": "window-v1"})
        await store.record_tombstone("source-a", 2)

        builder = IndexGenerationBuilder(store)
        waiting = await builder.reconcile_index_changes_page("generation-shadow", page_size=3)
        assert waiting["status"] == "waiting_for_ack"
        assert waiting["page_count"] == 0
        await store.acknowledge_generation_change("source-prebuild", 1, tombstoned=False)
        await store.acknowledge_generation_change("source-a", 1, tombstoned=False)
        await store.acknowledge_generation_change("source-b", 1, tombstoned=False)
        await store.acknowledge_generation_change("source-a", 2, tombstoned=True)
        first = await builder.reconcile_index_changes_page("generation-shadow", page_size=3)
        assert first["status"] == "in_progress"
        resumed_builder = IndexGenerationBuilder(store)
        second = await resumed_builder.reconcile_index_changes_page("generation-shadow", page_size=3)
        assert second["status"] == "caught_up"
        assert second["change_count"] == 4

        source_a = await store.get_generation_source("generation-shadow", "source-a")
        source_b = await store.get_generation_source("generation-shadow", "source-b")
        assert source_a["tombstoned"] is True
        assert source_a["high_sequence"] == 2
        assert source_b["tombstoned"] is False

        for capability in ("documents", "vectors", "memory", "knowledge"):
            await store.record_generation_checkpoint(
                "generation-shadow", capability, {"cursor": "snapshot-complete"}, sealed=True,
            )
        for capability in ("documents_reconciliation", "vectors_reconciliation",
                           "memory_reconciliation", "knowledge_reconciliation"):
            await store.record_generation_checkpoint(
                "generation-shadow", capability, {"cursor": second["high_watermark"]}, sealed=False,
            )
        await resumed_builder.fence_reconciled_generation("generation-shadow")
        blocked = await store.record_revision("source-c", 1, "revision-c", {"chunking": "window-v1"})
        assert blocked["decision"] == "fenced"
        assert await store.release_generation_write_fence("generation-shadow") is True
        accepted = await store.record_revision("source-c", 1, "revision-c", {"chunking": "window-v1"})
        assert accepted["decision"] == "accepted"

    asyncio.run(exercise())


def test_index_change_acknowledgement_rest_contract(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'generation-ack.db'}"
    migration_config = Config("alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")
    store = SqliteIndexStore(database_url)

    async def prepare():
        await store.initialize()
        await store.create_generation("generation-ack")
        await store.record_revision("source-ack", 1, "revision-ack", {"chunking": "window-v1"})

    asyncio.run(prepare())
    with TestClient(create_app(database_url=database_url)) as client:
        response = client.post("/api/v1/index/changes/acknowledge", json={
            "source_id": "source-ack",
            "revision_sequence": 1,
            "tombstoned": False,
        })
        assert response.status_code == 200
        assert response.json()["acknowledged"] == 1

    async def verify():
        changes = await store.list_generation_changes_after("generation-ack", 0, 10)
        assert len(changes) == 1
        assert changes[0]["acknowledged"] is True
        await store.close()

    asyncio.run(verify())


def test_generation_reconciles_updated_documents_and_vectors():
    index_store = InMemoryIndexStore()
    document_store = InMemoryDocumentStore()
    vector_store = InMemoryVectorStore()

    async def exercise():
        await index_store.create_generation("generation-doc-vector")
        first_document = Document("source-a", 1, "Source A", "original content", {"origin": "test"})
        first_chunk = Chunk(first_document.id, 0, "original content", [1.0, 0.0], start=0, end=16)
        await document_store.save(first_document, [first_chunk])
        await vector_store.upsert("knowledge_chunks", "test-model", 2, [{
            "id": "vector-a", "embedding": [1.0, 0.0], "document": "original content",
            "metadata": {"source_id": "source-a", "revision": 1},
        }])
        await index_store.record_revision("source-a", 1, "revision-a1", {"chunking": "window-v1"})
        await index_store.acknowledge_generation_change("source-a", 1, tombstoned=False)

        builder = IndexGenerationBuilder(index_store, document_store, vector_store)
        assert (await builder.copy_index_sources("generation-doc-vector", page_size=10))["status"] == "complete"
        assert (await builder.copy_documents_page("generation-doc-vector", page_size=10))["status"] == "complete"
        assert (await builder.copy_vectors_page("generation-doc-vector", page_size=10))["status"] == "complete"
        assert (await builder.reconcile_index_changes_page("generation-doc-vector", page_size=10))["status"] == "caught_up"
        assert (await builder.reconcile_document_changes_page("generation-doc-vector", page_size=10))["status"] == "caught_up"
        assert (await builder.reconcile_vector_changes_page("generation-doc-vector", page_size=10))["status"] == "caught_up"

        updated_document = Document("source-a", 2, "Source A", "updated content", {"origin": "test"})
        updated_chunk = Chunk(updated_document.id, 0, "updated content", [0.0, 1.0], start=0, end=15)
        await document_store.save(updated_document, [updated_chunk])
        await vector_store.upsert("knowledge_chunks", "test-model", 2, [{
            "id": "vector-a", "embedding": [0.0, 1.0], "document": "updated content",
            "metadata": {"source_id": "source-a", "revision": 2},
        }])
        await index_store.record_revision("source-a", 2, "revision-a2", {"chunking": "window-v1"})
        await index_store.acknowledge_generation_change("source-a", 2, tombstoned=False)

        assert (await builder.reconcile_index_changes_page("generation-doc-vector", page_size=10))["status"] == "caught_up"
        assert (await builder.reconcile_document_changes_page("generation-doc-vector", page_size=10))["status"] == "caught_up"
        assert (await builder.reconcile_vector_changes_page("generation-doc-vector", page_size=10))["status"] == "caught_up"

        generation_chunks = await document_store.generation_all_chunks("generation-doc-vector")
        assert len(generation_chunks) == 1
        assert generation_chunks[0].document.revision == 2
        assert generation_chunks[0].chunk.content == "updated content"
        vector_hits = await vector_store.query_generation(
            "generation-doc-vector", "knowledge_chunks", [0.0, 1.0], 10,
        )
        assert len(vector_hits) == 1
        assert vector_hits[0]["document"] == "updated content"

    asyncio.run(exercise())


def test_generation_reconciles_knowledge_updates_and_tombstones():
    index_store = InMemoryIndexStore()
    knowledge_store = InMemoryKnowledgeStore()

    async def exercise():
        await index_store.create_generation("generation-knowledge")
        await knowledge_store.put({
            "source_id": "knowledge-a", "revision_sequence": 1,
            "revision_id": "knowledge-revision-1", "title": "Knowledge A",
            "content": "first knowledge content", "origin": "test",
            "locator": "test://knowledge-a", "profiles": {"chunking": "window-v1"},
        })
        await index_store.record_revision("knowledge-a", 1, "knowledge-revision-1", {"knowledge": True})
        await index_store.acknowledge_generation_change("knowledge-a", 1, tombstoned=False)

        builder = IndexGenerationBuilder(index_store, knowledge_store=knowledge_store)
        assert (await builder.copy_index_sources("generation-knowledge", page_size=10))["status"] == "complete"
        assert (await builder.copy_knowledge_page("generation-knowledge", page_size=10))["status"] == "complete"
        assert (await builder.reconcile_index_changes_page("generation-knowledge", page_size=10))["status"] == "caught_up"
        assert (await builder.reconcile_knowledge_changes_page("generation-knowledge", page_size=10))["status"] == "caught_up"

        await knowledge_store.put({
            "source_id": "knowledge-a", "revision_sequence": 2,
            "revision_id": "knowledge-revision-2", "title": "Knowledge A",
            "content": "updated knowledge content", "origin": "test",
            "locator": "test://knowledge-a", "profiles": {"chunking": "window-v1"},
        })
        await index_store.record_revision("knowledge-a", 2, "knowledge-revision-2", {"knowledge": True})
        await index_store.acknowledge_generation_change("knowledge-a", 2, tombstoned=False)
        assert (await builder.reconcile_index_changes_page("generation-knowledge", page_size=10))["status"] == "caught_up"
        assert (await builder.reconcile_knowledge_changes_page("generation-knowledge", page_size=10))["status"] == "caught_up"
        current = await knowledge_store.get_generation("generation-knowledge", "knowledge-a")
        assert current["revision_sequence"] == 2
        assert current["content"] == "updated knowledge content"

        await index_store.record_tombstone("knowledge-a", 3)
        await knowledge_store.tombstone("knowledge-a")
        await index_store.acknowledge_generation_change("knowledge-a", 3, tombstoned=True)
        assert (await builder.reconcile_index_changes_page("generation-knowledge", page_size=10))["status"] == "caught_up"
        assert (await builder.reconcile_knowledge_changes_page("generation-knowledge", page_size=10))["status"] == "caught_up"
        tombstone = await knowledge_store.get_generation("generation-knowledge", "knowledge-a")
        assert tombstone["tombstoned"] is True

    asyncio.run(exercise())


def test_sqlite_knowledge_generation_storage_and_tombstones(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'knowledge-generations.db'}"
    migration_config = Config("alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")
    index_store = SqliteIndexStore(database_url)
    knowledge_store = SqliteKnowledgeStore(database_url)

    async def exercise():
        await index_store.initialize()
        await knowledge_store.initialize()
        await index_store.create_generation("generation-knowledge-sql")
        record = {
            "source_id": "knowledge-sql", "revision_sequence": 1,
            "revision_id": "revision-sql-1", "title": "Knowledge SQL",
            "content": "durable knowledge content", "origin": "test",
            "locator": "test://knowledge-sql", "profiles": {"chunking": "window-v1"},
        }
        await knowledge_store.put(record)
        active = await knowledge_store.get("knowledge-sql")
        await knowledge_store.upsert_generation("generation-knowledge-sql", active)
        shadow = await knowledge_store.get_generation("generation-knowledge-sql", "knowledge-sql")
        assert shadow["revision_sequence"] == 1
        assert shadow["provenance"]["locator"] == "test://knowledge-sql"
        await knowledge_store.tombstone_generation_source("generation-knowledge-sql", "knowledge-sql")
        tombstoned = await knowledge_store.get_generation("generation-knowledge-sql", "knowledge-sql")
        assert tombstoned["tombstoned"] is True
        assert await knowledge_store.delete_generation("generation-knowledge-sql") == 1
        await knowledge_store.close()
        await index_store.close()

    asyncio.run(exercise())
