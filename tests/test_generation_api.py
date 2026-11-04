import sqlite3

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from ensemble.api import create_app


def _client(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'generation-api.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return TestClient(create_app(database_url=database_url))


def test_generation_api_creates_build_and_reports_missing_capabilities(tmp_path):
    with _client(tmp_path) as client:
        created = client.post("/api/v1/index/generations", json={})
        assert created.status_code == 200
        generation_id = created.json()["generation"]["generation_id"]

        status = client.get("/api/v1/index/generations")
        assert status.status_code == 200
        build = status.json()["build"]
        assert build["generation_id"] == generation_id
        assert build["activation_ready"] is False
        assert "memory" in build["blocked_capabilities"]
        assert "memory" in build["missing_checkpoints"]
        assert "memory_reconciliation" in build["missing_checkpoints"]


def test_generation_api_runs_all_copy_and_reconciliation_passes_before_fence(tmp_path):
    with _client(tmp_path) as client:
        created = client.post(
            "/api/v1/index/generations",
            json={"generation_id": "generation-api-test"},
        )
        assert created.status_code == 200

        copied = client.post(
            "/api/v1/index/generations/generation-api-test/copy/index",
            json={"page_size": 10},
        )
        assert copied.status_code == 200
        assert copied.json()["status"] == "complete"
        assert copied.json()["page_count"] == 0

        for capability in ("documents", "vectors", "knowledge", "memory"):
            response = client.post(
                f"/api/v1/index/generations/generation-api-test/copy/{capability}",
                json={"page_size": 10},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "complete"

        for capability in ("index", "documents", "vectors", "knowledge", "memory"):
            response = client.post(
                f"/api/v1/index/generations/generation-api-test/reconcile/{capability}",
                json={"page_size": 10},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "caught_up"

        fenced = client.post(
            "/api/v1/index/generations/generation-api-test/fence",
        )
        assert fenced.status_code == 200
        assert fenced.json()["generation_id"] == "generation-api-test"
        status = client.get("/api/v1/index/generations")
        assert status.json()["build"]["activation_ready"] is True
        blocked_vector_write = client.put(
            "/api/v1/vector/collections/fenced_test/records",
            json={
                "ids": ["blocked-vector"], "embeddings": [[1.0, 0.0]],
                "documents": ["must not write while fenced"],
                "metadatas": [{"source_id": "fenced-source"}],
                "model": "test-model", "dimensions": 2,
            },
        )
        assert blocked_vector_write.status_code == 409



def test_generation_cutover_routes_retrieval_and_subsequent_writes(tmp_path):
    with _client(tmp_path) as client:
        first = client.post("/api/v1/ingest/documents", json={
            "source_id": "generation-source-1",
            "revision": 1,
            "title": "Generation source",
            "content": "generation cutover preserves searchable content",
            "chunk_profile": "window-v1",
            "media_type": "text/plain",
            "metadata": {"origin": "test", "locator": "test://generation-source-1"},
        })
        assert first.status_code == 201

        created = client.post(
            "/api/v1/index/generations",
            json={"generation_id": "generation-cutover-test"},
        )
        assert created.status_code == 200

        for capability in ("index", "documents", "vectors", "knowledge", "memory"):
            response = client.post(
                f"/api/v1/index/generations/generation-cutover-test/copy/{capability}",
                json={"page_size": 20},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "complete"

        for capability in ("index", "documents", "vectors", "knowledge", "memory"):
            response = client.post(
                f"/api/v1/index/generations/generation-cutover-test/reconcile/{capability}",
                json={"page_size": 20},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "caught_up"

        fenced = client.post("/api/v1/index/generations/generation-cutover-test/fence")
        assert fenced.status_code == 200
        activated = client.post("/api/v1/index/generations/generation-cutover-test/activate")
        assert activated.status_code == 200
        assert activated.json()["active_generation_id"] == "generation-cutover-test"
        retried = client.post("/api/v1/index/generations/generation-cutover-test/activate")
        assert retried.status_code == 200
        assert retried.json()["already_active"] is True

        before = client.post("/api/v1/search/retrieve", json={
            "query": "searchable content", "limit": 10, "rerank": False,
        })
        assert before.status_code == 200
        assert any(hit["source_id"] == "generation-source-1" for hit in before.json()["results"])

        knowledge = client.post("/api/v1/knowledge/records", json={
            "source_id": "generation-knowledge-source",
            "revision_sequence": 1,
            "revision_id": "knowledge-revision-1",
            "title": "Generation Knowledge",
            "content": "knowledge content remains available after generation cutover",
            "provenance": {"origin": "test", "locator": "test://generation-knowledge-source"},
            "profiles": {"chunking": "window-v1", "media_type": "text/plain"},
        })
        assert knowledge.status_code == 200
        with sqlite3.connect(tmp_path / "generation-api.db") as connection:
            knowledge_row = connection.execute(
                "SELECT tombstoned FROM ensemble_knowledge_generation_records "
                "WHERE generation_id = ? AND source_id = ?",
                ("generation-cutover-test", "generation-knowledge-source"),
            ).fetchone()
        assert knowledge_row == (0,)

        second = client.post("/api/v1/ingest/documents", json={
            "source_id": "generation-source-2",
            "revision": 1,
            "title": "Post cutover source",
            "content": "new writes enter the active generation",
            "chunk_profile": "window-v1",
            "media_type": "text/plain",
            "metadata": {"origin": "test", "locator": "test://generation-source-2"},
        })
        assert second.status_code == 201

        after = client.post("/api/v1/search/retrieve", json={
            "query": "active generation", "limit": 10, "rerank": False,
        })
        assert after.status_code == 200
        assert any(hit["source_id"] == "generation-source-2" for hit in after.json()["results"])

        vector_write = client.put(
            "/api/v1/vector/collections/generation_test/records",
            json={
                "ids": ["generation-vector-1"],
                "embeddings": [[1.0, 0.0]],
                "documents": ["generation vector content"],
                "metadatas": [{
                    "source_id": "generation-vector-source",
                    "url": "generation-vector-source",
                    "content_sha256": "a" * 64,
                }],
                "model": "test-model",
                "dimensions": 2,
            },
        )
        assert vector_write.status_code == 200
        vector_query = client.post(
            "/api/v1/vector/collections/generation_test/query",
            json={
                "embedding": [1.0, 0.0], "top_k": 5, "dimensions": 2,
                "generation_id": "generation-cutover-test",
            },
        )
        assert vector_query.status_code == 200
        assert vector_query.json()["results"][0]["id"] == "generation-vector-1"

        metadata_update = client.patch(
            "/api/v1/vector/collections/generation_test/records/metadata",
            json={
                "ids": ["generation-vector-1"],
                "metadatas": [{"source_id": "generation-vector-source", "url": "generation-vector-source", "label": "updated"}],
            },
        )
        assert metadata_update.status_code == 200
        vector_query = client.post(
            "/api/v1/vector/collections/generation_test/query",
            json={
                "embedding": [1.0, 0.0], "top_k": 5, "dimensions": 2,
                "generation_id": "generation-cutover-test",
            },
        )
        assert vector_query.json()["results"][0]["metadata"]["label"] == "updated"

        vector_prune = client.post(
            "/api/v1/vector/collections/generation_test/records/prune",
            json={"url": "generation-vector-source", "keep_sha256": "0" * 64},
        )
        assert vector_prune.status_code == 200
        vector_query = client.post(
            "/api/v1/vector/collections/generation_test/query",
            json={
                "embedding": [1.0, 0.0], "top_k": 5, "dimensions": 2,
                "generation_id": "generation-cutover-test",
            },
        )
        assert vector_query.json()["results"] == []

        # Simulate an Index tombstone committed while downstream generation
        # cleanup has not yet completed. Retrieval must still fail closed.
        with sqlite3.connect(tmp_path / "generation-api.db") as connection:
            connection.execute(
                "UPDATE ensemble_index_sources SET tombstoned = 1 WHERE source_id = ?",
                ("generation-source-1",),
            )
        fail_closed = client.post("/api/v1/search/retrieve", json={
            "query": "searchable content", "limit": 10, "rerank": False,
        })
        assert fail_closed.status_code == 200
        assert all(hit["source_id"] != "generation-source-1" for hit in fail_closed.json()["results"])

        tombstone = client.post("/api/v1/index/tombstones", json={
            "source_id": "generation-source-1", "revision_sequence": 2,
        })
        assert tombstone.status_code == 200
        assert tombstone.json()["cleanup"]["status"] == "complete"

        knowledge_tombstone = client.post("/api/v1/index/tombstones", json={
            "source_id": "generation-knowledge-source", "revision_sequence": 2,
        })
        assert knowledge_tombstone.status_code == 200
        assert knowledge_tombstone.json()["cleanup"]["status"] == "complete"
        with sqlite3.connect(tmp_path / "generation-api.db") as connection:
            knowledge_row = connection.execute(
                "SELECT tombstoned FROM ensemble_knowledge_generation_records "
                "WHERE generation_id = ? AND source_id = ?",
                ("generation-cutover-test", "generation-knowledge-source"),
            ).fetchone()
        assert knowledge_row == (1,)

        after_delete = client.post("/api/v1/search/retrieve", json={
            "query": "searchable content", "limit": 10, "rerank": False,
        })
        assert after_delete.status_code == 200
        assert all(hit["source_id"] != "generation-source-1" for hit in after_delete.json()["results"])



def test_base_retrieval_fails_closed_when_index_tombstone_precedes_cleanup(tmp_path):
    with _client(tmp_path) as client:
        ingested = client.post("/api/v1/ingest/documents", json={
            "source_id": "base-freshness-source",
            "revision": 1,
            "title": "Base freshness",
            "content": "index authority blocks stale base retrieval",
            "chunk_profile": "window-v1",
            "media_type": "text/plain",
            "metadata": {"origin": "test", "locator": "test://base-freshness-source"},
        })
        assert ingested.status_code == 201

        before = client.post("/api/v1/search/retrieve", json={
            "query": "stale base retrieval", "limit": 10, "rerank": False,
        })
        assert any(hit["source_id"] == "base-freshness-source" for hit in before.json()["results"])

        # Simulate the Index commit succeeding while document cleanup is delayed.
        with sqlite3.connect(tmp_path / "generation-api.db") as connection:
            connection.execute(
                "UPDATE ensemble_index_sources SET tombstoned = 1 WHERE source_id = ?",
                ("base-freshness-source",),
            )

        after = client.post("/api/v1/search/retrieve", json={
            "query": "stale base retrieval", "limit": 10, "rerank": False,
        })
        assert after.status_code == 200
        assert all(hit["source_id"] != "base-freshness-source" for hit in after.json()["results"])
