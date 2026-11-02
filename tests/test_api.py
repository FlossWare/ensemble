import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from ensemble.api import create_app


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
        response = client.post("/api/v1/documents", json={
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
        response = client.post("/api/v1/documents", json={})
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
        first = client.post("/api/v1/documents", json=payload).json()

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
        first = client.post("/api/v1/documents", json=original).json()
        replacement = {
            **original,
            "content": "The replacement content contains current terminology.",
        }
        second = client.post("/api/v1/documents", json=replacement).json()
        assert second["id"] == first["id"]

        old_hits = client.post("/api/v1/search", json={"query": "obsolete"}).json()
        new_hits = client.post("/api/v1/search", json={"query": "current terminology"}).json()
        assert old_hits == []
        assert len(new_hits) == 1
        assert new_hits[0]["text"] == replacement["content"]


def test_reingest_is_idempotent_and_latest_revision_is_searchable(tmp_path):
    with _client(tmp_path) as client:
        original = {
            "source_id": "versioned-note",
            "revision": 1,
            "title": "First revision",
            "content": "The original document discusses obsolete indexing behavior.",
        }
        first = client.post("/api/v1/documents", json=original).json()
        again = client.post("/api/v1/documents", json=original).json()
        assert again["id"] == first["id"]

        revised = {
            "source_id": "versioned-note",
            "revision": 2,
            "title": "Second revision",
            "content": "The current document discusses durable indexing behavior.",
        }
        second = client.post("/api/v1/documents", json=revised).json()
        assert second["id"] != first["id"]

        current = client.post("/api/v1/search", json={"query": "indexing behavior"}).json()
        assert len(current) == 1
        assert current[0]["revision"] == 2
        assert current[0]["document_id"] == second["id"]

        historical = client.post("/api/v1/documents", json=original).json()
        assert historical["id"] == first["id"]
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
        upsert = client.post("/api/v1/vector/collections/knowledge/upsert", json=payload)
        assert upsert.status_code == 200
        assert upsert.json()["upserted"] == 2

        existing = client.post(
            "/api/v1/vector/collections/knowledge/existing",
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
        assert client.post("/api/v1/vector/collections/c/upsert", json=base).status_code == 200
        replacement = {**base, "documents": ["replacement"], "metadatas": [{"version": 2}]}
        assert client.post("/api/v1/vector/collections/c/upsert", json=replacement).json()["upserted"] == 1
        result = client.post(
            "/api/v1/vector/collections/c/query",
            json={"embedding": [1.0, 0.0]},
        ).json()["results"]
        assert len(result) == 1
        assert result[0]["document"] == "replacement"

        mismatch = {**base, "model": "model-b"}
        assert client.post("/api/v1/vector/collections/c/upsert", json=mismatch).status_code == 409


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
        client.post("/api/v1/vector/collections/c/upsert", json=payload)
        updated = client.post(
            "/api/v1/vector/collections/c/metadata",
            json={"ids": ["current", "missing"], "metadatas": [{"tag": "new"}, {"tag": "none"}]},
        )
        assert updated.json()["updated"] == 1
        deleted = client.post(
            "/api/v1/vector/collections/c/delete-superseded",
            json={"url": "https://example.test/source", "keep_sha256": "b" * 64},
        )
        assert deleted.json()["deleted"] == 1
        found = client.post(
            "/api/v1/vector/collections/c/existing",
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
        assert client.post("/api/v1/vector/collections/c/upsert", json=payload).status_code == 200

    with _client(tmp_path) as client:
        result = client.post(
            "/api/v1/vector/collections/c/query",
            json={"embedding": [0.5, 0.5]},
        ).json()["results"]
        assert result[0]["id"] == "persistent"


def test_vector_contract_rejects_invalid_vectors(tmp_path):
    with _client(tmp_path) as client:
        invalid = '{"model":"model-a","dimensions":2,"ids":["bad"],"embeddings":[[NaN,0.0]],"documents":["bad"],"metadatas":[{"source":"test"}]}'
        response = client.post(
            "/api/v1/vector/collections/c/upsert",
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
        ingested = client.post("/api/v1/documents", json=payload)
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
        response = client.post("/api/v1/documents", json={
            "source_id": "invalid-profile",
            "revision": 1,
            "title": "Invalid profile",
            "content": "This should not be ingested.",
            "chunk_profile": "not-a-profile",
        })
        assert response.status_code == 422
