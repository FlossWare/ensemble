import asyncio
import json
import sqlite3

import pytest

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.index_store import SqliteIndexStore
from ensemble.memory_store import SqliteMemoryStore
from ensemble.rebuild import IndexGenerationBuilder


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
        blocked_vector_write = client.put(
            "/api/v1/vector/collections/building_test/records",
            json={
                "ids": ["blocked-vector"], "embeddings": [[1.0, 0.0]],
                "documents": ["untracked vector mutation"],
                "metadatas": [{"source_id": "untracked-vector-source"}],
                "model": "test-model", "dimensions": 2,
            },
        )
        assert blocked_vector_write.status_code == 409


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
        memory_name = "generation-memory"
        memory_before = client.put(
            f"/api/v1/memory/memories/{memory_name}",
            json={"content": "memory before generation rebuild"},
        )
        assert memory_before.status_code == 200

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

        memory_during_build = client.put(
            f"/api/v1/memory/memories/{memory_name}",
            json={"content": "memory updated during generation rebuild"},
        )
        assert memory_during_build.status_code == 200

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

        with sqlite3.connect(tmp_path / "generation-api.db") as connection:
            memory_row = connection.execute(
                "SELECT snapshot FROM ensemble_memory_generation_records "
                "WHERE generation_id = ? AND name = ?",
                ("generation-cutover-test", memory_name),
            ).fetchone()
        assert memory_row is not None
        assert json.loads(memory_row[0])["content"] == "memory updated during generation rebuild"

        memory_after_cutover = client.put(
            f"/api/v1/memory/memories/{memory_name}",
            json={"content": "memory updated after generation cutover"},
        )
        assert memory_after_cutover.status_code == 200
        with sqlite3.connect(tmp_path / "generation-api.db") as connection:
            memory_row = connection.execute(
                "SELECT snapshot FROM ensemble_memory_generation_records "
                "WHERE generation_id = ? AND name = ?",
                ("generation-cutover-test", memory_name),
            ).fetchone()
        assert memory_row is not None
        assert json.loads(memory_row[0])["content"] == "memory updated after generation cutover"

        memory_deleted = client.delete(
            f"/api/v1/memory/memories/{memory_name}",
        )
        assert memory_deleted.status_code == 200
        with sqlite3.connect(tmp_path / "generation-api.db") as connection:
            memory_row = connection.execute(
                "SELECT snapshot FROM ensemble_memory_generation_records "
                "WHERE generation_id = ? AND name = ?",
                ("generation-cutover-test", memory_name),
            ).fetchone()
        assert memory_row is None

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
        assert tombstone.json()["reclamation"]["source_id"] == "generation-source-1"
        assert tombstone.json()["reclamation"]["state"] in {"pending", "running", "complete"}
        queue_status = client.get("/api/v1/index/reclamation")
        assert queue_status.status_code == 200
        assert sum(queue_status.json()["jobs"].values()) >= 1

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

        reclaimed = client.post("/api/v1/index/reclamation/run")
        assert reclaimed.status_code == 200
        assert reclaimed.json()["processed"] == 2
        assert reclaimed.json()["jobs"]["complete"] == 2
        source_state = client.get("/api/v1/index/sources/generation-source-1")
        assert source_state.status_code == 200
        assert source_state.json()["source"]["purged"] is True

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



def test_generation_fence_recovery_fails_closed_for_unexpected_generation_state(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'generation-api.db'}"
    with _client(tmp_path):
        pass

    async def prepare_fence():
        store = SqliteIndexStore(database_url)
        await store.initialize()
        await store.create_generation("recovery-candidate")
        await store.acquire_generation_write_fence("recovery-candidate", 0)
        await store.close()

    asyncio.run(prepare_fence())
    with sqlite3.connect(tmp_path / "generation-api.db") as connection:
        connection.execute(
            "UPDATE ensemble_index_generations SET state = 'retired' "
            "WHERE generation_id = ?",
            ("recovery-candidate",),
        )

    async def recover():
        store = SqliteIndexStore(database_url)
        await store.initialize()
        try:
            with pytest.raises(RuntimeError, match="refusing unsafe recovery"):
                await store.recover_generation_write_fence()
        finally:
            await store.close()

    asyncio.run(recover())
    with sqlite3.connect(tmp_path / "generation-api.db") as connection:
        fence = connection.execute(
            "SELECT generation_id FROM ensemble_index_generation_write_fence "
            "WHERE singleton_id = 1"
        ).fetchone()
    assert fence == ("recovery-candidate",)



def test_memory_generation_reconciliation_replays_updates_and_deletions(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'generation-memory.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    async def scenario():
        index = SqliteIndexStore(database_url)
        memory = SqliteMemoryStore(database_url)
        await index.initialize()
        await memory.initialize()
        try:
            await memory.write("rebuild-memory", "before rebuild")
            await index.create_generation("memory-reconciliation-test")
            builder = IndexGenerationBuilder(index, memory_store=memory)

            copied_index = await builder.copy_index_sources("memory-reconciliation-test")
            assert copied_index["status"] == "complete"
            copied_memory = await builder.copy_memory_page("memory-reconciliation-test")
            assert copied_memory["status"] == "complete"
            initial_snapshot = await memory.get_memory_generation_snapshot(
                "memory-reconciliation-test", "rebuild-memory",
            )
            assert initial_snapshot["content"] == "before rebuild"

            await memory.write("rebuild-memory", "updated during rebuild")
            updated = await builder.reconcile_memory_changes_page(
                "memory-reconciliation-test",
            )
            assert updated["status"] == "caught_up"
            snapshot = await memory.get_memory_generation_snapshot(
                "memory-reconciliation-test", "rebuild-memory",
            )
            assert snapshot["content"] == "updated during rebuild"

            await memory.delete("rebuild-memory")
            deleted = await builder.reconcile_memory_changes_page(
                "memory-reconciliation-test",
            )
            assert deleted["status"] == "caught_up"
            assert await memory.get_memory_generation_snapshot(
                "memory-reconciliation-test", "rebuild-memory",
            ) is None
            assert await memory.pending_memory_generation_changes(
                "memory-reconciliation-test",
            ) == 0
        finally:
            await memory.close()
            await index.close()

    asyncio.run(scenario())



def test_generation_fence_recovery_reopens_building_generation_and_invalidates_reconciliation(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'generation-recovery.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    async def scenario():
        store = SqliteIndexStore(database_url)
        memory = SqliteMemoryStore(database_url)
        await store.initialize()
        await memory.initialize()
        try:
            await store.create_generation("recoverable-generation")
            await store.record_generation_checkpoint(
                "recoverable-generation", "index_reconciliation",
                {"cursor": 0, "observed_high_watermark": 0}, sealed=True,
            )
            await store.acquire_generation_write_fence("recoverable-generation", 0)
            with pytest.raises(RuntimeError, match="writes are fenced"):
                await memory.write("fenced-memory", "must not commit")

            recovered = await store.recover_generation_write_fence()
            assert recovered["recovered"] is True
            assert recovered["generation_state"] == "building"

            status = await store.generation_status()
            generation = next(
                item for item in status["generations"]
                if item["generation_id"] == "recoverable-generation"
            )
            assert generation["checkpoints"]["index_reconciliation"]["sealed"] is False

            accepted = await store.record_revision(
                "after-restart-source", 1, "revision-1", {"profile": "default"},
            )
            assert accepted["decision"] == "accepted"
            await memory.write("fenced-memory", "writes resumed after recovery")
            assert await memory.read("fenced-memory") == "writes resumed after recovery"
        finally:
            await memory.close()
            await store.close()

    asyncio.run(scenario())



def test_application_startup_recovers_interrupted_generation_fence(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'generation-startup-recovery.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    async def leave_interrupted_cutover():
        store = SqliteIndexStore(database_url)
        await store.initialize()
        await store.create_generation("startup-recovery-generation")
        await store.record_generation_checkpoint(
            "startup-recovery-generation", "index_reconciliation",
            {"cursor": 0, "observed_high_watermark": 0}, sealed=True,
        )
        await store.acquire_generation_write_fence(
            "startup-recovery-generation", through_change_id=0,
        )
        await store.close()

    asyncio.run(leave_interrupted_cutover())

    # The application lifespan is the production recovery path. Starting the
    # app must release the abandoned fence and invalidate every sealed
    # reconciliation checkpoint before accepting traffic.
    with TestClient(create_app(database_url=database_url)):
        pass
    # A second startup must be a no-op once the abandoned fence is gone.
    with TestClient(create_app(database_url=database_url)):
        pass

    with sqlite3.connect(tmp_path / "generation-startup-recovery.db") as connection:
        fence_count = connection.execute(
            "SELECT COUNT(*) FROM ensemble_index_generation_write_fence"
        ).fetchone()[0]
        generation = connection.execute(
            "SELECT state, checkpoints FROM ensemble_index_generations "
            "WHERE generation_id = ?",
            ("startup-recovery-generation",),
        ).fetchone()

    assert fence_count == 0
    assert generation is not None
    assert generation[0] == "building"
    checkpoints = json.loads(generation[1])
    assert checkpoints["index_reconciliation"]["sealed"] is False



def test_activation_rejects_incomplete_generation_without_changing_active_pointer(tmp_path):
    with _client(tmp_path) as client:
        before = client.get("/api/v1/index/generations")
        assert before.status_code == 200
        active_before = before.json()["active_generation_id"]
        epoch_before = before.json()["epoch"]

        created = client.post(
            "/api/v1/index/generations",
            json={"generation_id": "incomplete-activation"},
        )
        assert created.status_code == 200

        rejected = client.post(
            "/api/v1/index/generations/incomplete-activation/activate",
        )
        assert rejected.status_code == 409
        assert rejected.json()["detail"]

        after = client.get("/api/v1/index/generations")
        assert after.status_code == 200
        assert after.json()["active_generation_id"] == active_before
        assert after.json()["epoch"] == epoch_before
        target = next(
            item for item in after.json()["generations"]
            if item["generation_id"] == "incomplete-activation"
        )
        assert target["state"] == "building"



def test_failed_activation_rolls_back_pointer_state_and_fence(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'generation-atomic-activation.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    required = (
        "index", "documents", "vectors", "memory", "knowledge",
        "index_reconciliation", "documents_reconciliation",
        "vectors_reconciliation", "memory_reconciliation",
        "knowledge_reconciliation",
    )
    verification = {
        "acknowledged_writes_verified": True,
        "tombstones_verified": True,
        "reconciled_through_change_id": 0,
    }

    async def exercise():
        store = SqliteIndexStore(database_url)
        await store.initialize()

        async def prepare(generation_id):
            await store.create_generation(generation_id)
            for capability in required:
                await store.record_generation_checkpoint(
                    generation_id, capability, {"cursor": 0}, sealed=True,
                )
            await store.acquire_generation_write_fence(generation_id, 0)

        initial = await store.generation_status()
        await prepare("generation-original")
        await store.activate_generation(
            "generation-original", initial["active_generation_id"],
            initial["epoch"], verification,
        )
        await prepare("generation-target")
        try:
            await store.activate_generation(
                "generation-target", "generation-original",
                initial["epoch"] + 1, verification,
            )
        except Exception:
            pass
        else:
            raise AssertionError("simulated activation failure was not raised")

        status = await store.generation_status()
        states = {
            item["generation_id"]: item["state"]
            for item in status["generations"]
        }
        assert status["active_generation_id"] == "generation-original"
        assert status["epoch"] == initial["epoch"] + 1
        assert states["generation-original"] == "active"
        assert states["generation-target"] == "building"
        with pytest.raises(RuntimeError, match="writes are fenced"):
            await store.assert_generation_writes_open()
        await store.close()

    with sqlite3.connect(tmp_path / "generation-atomic-activation.db") as connection:
        connection.execute(
            "CREATE TRIGGER reject_target_activation "
            "BEFORE UPDATE OF state ON ensemble_index_generations "
            "WHEN NEW.generation_id = 'generation-target' AND NEW.state = 'active' "
            "BEGIN SELECT RAISE(ABORT, 'simulated activation failure'); END"
        )
        connection.commit()

    asyncio.run(exercise())

def test_knowledge_generation_mirror_failure_keeps_index_change_retryable(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'knowledge-generation-retry.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    app = create_app(database_url=database_url)
    knowledge_store = app.state.knowledge_store
    original_upsert = knowledge_store.upsert_generation
    fail_once = {"enabled": True}

    async def upsert_generation(generation_id, record):
        if fail_once["enabled"]:
            fail_once["enabled"] = False
            raise RuntimeError("simulated Knowledge generation mirror failure")
        return await original_upsert(generation_id, record)

    knowledge_store.upsert_generation = upsert_generation

    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.post(
            "/api/v1/index/generations",
            json={"generation_id": "knowledge-generation-retry"},
        )
        assert created.status_code == 200

        for capability in ("index", "documents", "vectors", "knowledge", "memory"):
            copied = client.post(
                f"/api/v1/index/generations/knowledge-generation-retry/copy/{capability}",
                json={"page_size": 20},
            )
            assert copied.status_code == 200

        for capability in ("index", "documents", "vectors", "knowledge", "memory"):
            reconciled = client.post(
                f"/api/v1/index/generations/knowledge-generation-retry/reconcile/{capability}",
                json={"page_size": 20},
            )
            assert reconciled.status_code == 200
            assert reconciled.json()["status"] == "caught_up"

        assert client.post(
            "/api/v1/index/generations/knowledge-generation-retry/fence"
        ).status_code == 200
        activated = client.post(
            "/api/v1/index/generations/knowledge-generation-retry/activate"
        )
        assert activated.status_code == 200

        payload = {
            "source_id": "knowledge-mirror-retry",
            "revision_sequence": 1,
            "revision_id": "knowledge-mirror-retry-rev-1",
            "title": "Knowledge mirror retry",
            "content": "Knowledge publication must remain retryable after a mirror failure.",
            "provenance": {
                "origin": "test",
                "locator": "test://knowledge-mirror-retry",
            },
            "profiles": {"chunking": "window-v1@1"},
        }
        failed = client.post("/api/v1/knowledge/records", json=payload)
        assert failed.status_code == 500

        # The base Knowledge write may have succeeded, but the generation mirror
        # did not. The Index journal must therefore remain unacknowledged.
        changes = asyncio.run(
            app.state.index_store.list_generation_changes_after(
                "knowledge-generation-retry", 0, 20,
            )
        )
        change = next(item for item in changes if item["source_id"] == payload["source_id"])
        assert change["acknowledged"] is False
        assert client.get(
            "/api/v1/knowledge/records/knowledge-mirror-retry"
        ).status_code == 200

        retried = client.post("/api/v1/knowledge/records", json=payload)
        assert retried.status_code == 200
        assert retried.json()["decision"] == "duplicate"

        changes = asyncio.run(
            app.state.index_store.list_generation_changes_after(
                "knowledge-generation-retry", 0, 20,
            )
        )
        change = next(item for item in changes if item["source_id"] == payload["source_id"])
        assert change["acknowledged"] is True
        generation_record = asyncio.run(
            app.state.knowledge_store.get_generation(
                "knowledge-generation-retry", payload["source_id"],
            )
        )
        assert generation_record is not None
        assert generation_record["revision_id"] == payload["revision_id"]
