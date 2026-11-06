import asyncio
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from alembic import command
from alembic.config import Config

from ensemble.index_store import SqliteIndexStore
from ensemble.api import create_app
from ensemble.graph_memory import InMemoryGraphStore
from ensemble.graph_store import SqliteGraphStore
from ensemble.index_memory import InMemoryIndexStore
from ensemble.store import InMemoryDocumentStore
from ensemble.knowledge_store import SqliteKnowledgeStore
from ensemble.memory_store import InMemoryMemoryStore
from ensemble.models import Chunk, Document
from ensemble.reclamation import InMemoryReclamationStore, SqliteReclamationStore
from ensemble.store import SqlDocumentStore, generation_documents
from ensemble.vector_store import SqliteVectorStore
from ensemble.vector_memory import InMemoryVectorStore


def test_source_reclamation_queue_is_durable_and_idempotent(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'reclamation.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    queue = SqliteReclamationStore(database_url)

    async def exercise():
        await queue.initialize()
        first = await queue.enqueue("source-1")
        assert first["state"] == "pending"
        duplicate = await queue.enqueue("source-1")
        assert duplicate["state"] == "pending"
        jobs = await queue.claim_due(10)
        assert [job["source_id"] for job in jobs] == ["source-1"]
        assert jobs[0]["attempts"] == 1
        await queue.complete("source-1")
        assert await queue.status() == {
            "pending": 0, "running": 0, "retry": 0, "complete": 1,
        }
        # A completed reclamation is not re-enqueued by a repeated tombstone.
        assert (await queue.enqueue("source-1"))["state"] == "complete"

        await queue.enqueue("source-2")
        claimed = await queue.claim_due(10)
        assert [job["source_id"] for job in claimed] == ["source-2"]
        await queue.retry("source-2", "transient store failure")
        assert (await queue.status())["retry"] == 1

        # Make the retry due, then simulate a worker crash after it claims the job.
        with sqlite3.connect(tmp_path / "reclamation.db") as connection:
            connection.execute(
                "UPDATE ensemble_source_reclamation SET available_at = ? WHERE source_id = ?",
                ("2000-01-01 00:00:00", "source-2"),
            )
        retried = await queue.claim_due(10)
        assert retried[0]["attempts"] == 2
        with sqlite3.connect(tmp_path / "reclamation.db") as connection:
            connection.execute(
                "UPDATE ensemble_source_reclamation SET updated_at = ? WHERE source_id = ?",
                ("2000-01-01 00:00:00", "source-2"),
            )
        recovered = await queue.claim_due(10)
        assert recovered[0]["attempts"] == 3
        await queue.complete("source-2")
        assert (await queue.status())["complete"] == 2
        await queue.close()

    asyncio.run(exercise())


def test_reclamation_physically_removes_active_source_data_but_not_index_tombstone(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'source-data.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    documents = SqlDocumentStore(database_url)
    vectors = SqliteVectorStore(database_url)
    knowledge = SqliteKnowledgeStore(database_url)
    index = SqliteIndexStore(database_url)

    async def exercise():
        await documents.initialize()
        await vectors.initialize()
        await knowledge.initialize()
        await index.initialize()

        await index.record_revision("source-1", 1, "revision-1", {"document": "window-v1"})
        document = Document("source-1", 1, "Title", "source content")
        await documents.save(document, [
            Chunk(document.id, 0, "source content", [1.0, 0.0]),
        ])
        await vectors.upsert("test", "test-model", 2, [{
            "id": "record-1", "embedding": [1.0, 0.0],
            "document": "source content",
            "metadata": {"source_id": "source-1"},
        }])
        await knowledge.put({
            "source_id": "source-1", "revision_sequence": 1,
            "revision_id": "revision-1", "title": "Title",
            "content": "source content", "origin": "test",
            "locator": "test://source-1", "profiles": {},
        })

        await index.record_tombstone("source-1", 2)
        await documents.tombstone_source("source-1")
        await knowledge.tombstone("source-1")
        # Logical deletion takes effect before reclamation.
        assert await documents.all_chunks() == []
        assert (await knowledge.get("source-1"))["tombstoned"] is True
        assert (await index.get_source("source-1"))["tombstoned"] is True
        # Raw Vector queries also consult the authoritative Index tombstone.
        assert await vectors.query("test", [1.0, 0.0], 10) == []

        # Soft deletion is reversible because physical data is still present.
        restored = await index.restore_source("source-1", 3)
        assert restored["tombstoned"] is False
        await documents.restore_source("source-1")
        await knowledge.restore("source-1")
        assert len(await documents.all_chunks()) == 1
        assert (await knowledge.get("source-1"))["content"] == "source content"
        assert len(await vectors.query("test", [1.0, 0.0], 10)) == 1

        # A later deletion hides it again; permanent purge is a separate action.
        await index.record_tombstone("source-1", 4)
        await documents.tombstone_source("source-1")
        await knowledge.tombstone("source-1")
        assert await documents.all_chunks() == []
        assert (await knowledge.get("source-1"))["tombstoned"] is True
        assert await vectors.query("test", [1.0, 0.0], 10) == []

        # Physical cleanup completes before Index marks the tombstone permanent.
        assert await documents.reclaim_source("source-1") == 1
        assert await knowledge.reclaim_source("source-1") == 1
        assert await vectors.reclaim_source("source-1") == 1
        await index.mark_purged("source-1")
        await index.mark_purged("source-1")  # retry after an uncertain completion is idempotent
        try:
            await index.restore_source("source-1", 5)
            raise AssertionError("permanently purged source was restored")
        except RuntimeError:
            pass

        assert await documents.all_chunks() == []
        assert await knowledge.get("source-1") is None
        assert await vectors.query("test", [1.0, 0.0], 10) == []
        # The authoritative tombstone survives physical cleanup.
        assert (await index.get_source("source-1"))["tombstoned"] is True

        await index.close()
        await knowledge.close()
        await vectors.close()
        await documents.close()

    asyncio.run(exercise())



def test_generation_source_reclamation_is_bounded_and_retryable(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'generation-reclaim-pages.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    index = SqliteIndexStore(database_url)
    documents = SqlDocumentStore(database_url)
    vectors = SqliteVectorStore(database_url)

    async def exercise():
        await index.initialize()
        await documents.initialize()
        await vectors.initialize()
        await index.create_generation("bounded-reclaim")

        now = datetime.now(timezone.utc)
        rows = [{
            "generation_id": "bounded-reclaim",
            "document_id": str(uuid4()),
            "source_id": "large-source",
            "revision": revision,
            "title": "Large source",
            "content": f"revision {revision}",
            "metadata": {},
            "created_at": now,
            "is_current": False,
        } for revision in range(1, 502)]
        async with documents.engine.begin() as connection:
            await connection.execute(generation_documents.insert(), rows)

        vector_items = [{
            "id": f"large-vector-{index:04d}",
            "embedding": [1.0, 0.0],
            "document": "large source vector",
            "metadata": {"source_id": "large-source"},
        } for index in range(501)]
        await vectors.upsert_generation(
            "bounded-reclaim", "bounded-reclaim-vectors",
            "test-model", 2, vector_items,
        )

        # 501 is the explicit "more pages remain" sentinel after deleting 500.
        assert await documents.reclaim_generation_source(
            "bounded-reclaim", "large-source",
        ) == 501
        assert await documents.reclaim_generation_source(
            "bounded-reclaim", "large-source",
        ) == 1
        assert await documents.reclaim_generation_source(
            "bounded-reclaim", "large-source",
        ) == 0

        assert await vectors.delete_generation_source(
            "bounded-reclaim", "large-source",
        ) == 501
        assert await vectors.delete_generation_source(
            "bounded-reclaim", "large-source",
        ) == 1
        assert await vectors.delete_generation_source(
            "bounded-reclaim", "large-source",
        ) == 0

        await vectors.close()
        await documents.close()
        await index.close()

    asyncio.run(exercise())


def test_inmemory_generation_vector_reclamation_is_bounded():
    store = InMemoryVectorStore()

    async def exercise():
        items = [{
            "id": f"record-{index:04d}",
            "embedding": [1.0, 0.0],
            "document": "source",
            "metadata": {"source_id": "large-source"},
        } for index in range(501)]
        await store.upsert_generation("generation", "vectors", "test-model", 2, items)
        assert await store.delete_generation_source("generation", "large-source") == 501
        assert await store.delete_generation_source("generation", "large-source") == 1
        assert await store.delete_generation_source("generation", "large-source") == 0

    asyncio.run(exercise())


def test_reclamation_can_use_short_deferral_for_generation_builds():
    queue = InMemoryReclamationStore()

    async def exercise():
        await queue.enqueue("deferred-source")
        assert len(await queue.claim_due()) == 1
        await queue.retry("deferred-source", "generation build in progress", delay_seconds=15)
        assert (await queue.status())["retry"] == 1
        job = queue._jobs["deferred-source"]
        remaining = (job["available_at"] - job["updated_at"]).total_seconds()
        assert remaining == 15

    asyncio.run(exercise())



def test_reclamation_worker_retries_partial_failure_before_marking_source_purged():
    class FailOnceVectorStore(InMemoryVectorStore):
        def __init__(self):
            super().__init__()
            self.fail_next_reclamation = True

        async def reclaim_source(self, source_id):
            if self.fail_next_reclamation:
                self.fail_next_reclamation = False
                raise RuntimeError("injected vector-store failure")
            return await super().reclaim_source(source_id)

    vectors = FailOnceVectorStore()
    app = create_app(
        store=InMemoryDocumentStore(),
        vector_store=vectors,
        index_store=InMemoryIndexStore(),
        graph_store=InMemoryGraphStore(),
    )

    async def exercise():
        index = app.state.index_store
        queue = app.state.reclamation_store
        await index.record_revision("partial-source", 1, "revision-1", {})
        await index.record_tombstone("partial-source", 2)
        await queue.enqueue("partial-source")

        # Earlier stores may already have been cleaned when Vector fails.
        # The worker must retain the authoritative tombstone and retry.
        assert await app.state.reclamation_runner() == 1
        source = await index.get_source("partial-source")
        assert source["tombstoned"] is True
        assert source["purged"] is False
        assert (await queue.status())["retry"] == 1
        assert "injected vector-store failure" in queue._jobs["partial-source"]["last_error"]

        # Make the retry immediately due, then verify idempotent completion.
        from datetime import datetime, timezone
        queue._jobs["partial-source"]["available_at"] = datetime.now(timezone.utc)
        assert await app.state.reclamation_runner() == 1
        source = await index.get_source("partial-source")
        assert source["tombstoned"] is True
        assert source["purged"] is True
        assert (await queue.status())["complete"] == 1

    asyncio.run(exercise())



def test_inmemory_generation_document_reclamation_is_bounded():
    from ensemble.models import Document

    store = InMemoryDocumentStore()

    async def exercise():
        for revision in range(1, 502):
            document = Document(
                source_id="large-source", revision=revision,
                title="Large source", content=f"revision {revision}",
            )
            await store.upsert_generation("generation", document, [])
        assert await store.reclaim_generation_source(
            "generation", "large-source",
        ) == 501
        assert await store.reclaim_generation_source(
            "generation", "large-source",
        ) == 1
        assert await store.reclaim_generation_source(
            "generation", "large-source",
        ) == 0

    asyncio.run(exercise())


def test_sqlite_reclamation_claims_are_exclusive_across_workers(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'reclamation-workers.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    first_worker = SqliteReclamationStore(database_url)
    second_worker = SqliteReclamationStore(database_url)

    async def exercise():
        await first_worker.initialize()
        await second_worker.initialize()
        for index in range(40):
            await first_worker.enqueue(f"source-{index:02d}")

        first_batch, second_batch = await asyncio.gather(
            first_worker.claim_due(40),
            second_worker.claim_due(40),
        )
        first_ids = {job["source_id"] for job in first_batch}
        second_ids = {job["source_id"] for job in second_batch}

        assert first_ids.isdisjoint(second_ids)
        assert first_ids | second_ids == {
            f"source-{index:02d}" for index in range(40)
        }
        assert len(first_ids) + len(second_ids) == 40

        await first_worker.close()
        await second_worker.close()

    asyncio.run(exercise())


def test_sqlite_reclamation_removes_source_graph_materialization_but_retains_shared_entities(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'reclamation-graph.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    graph = SqliteGraphStore(database_url)

    async def exercise():
        await graph.initialize()
        source = await graph.add_node("source", {"source_id": "source-graph-1"})
        entity = await graph.add_node("entity", {"canonical": "Ada Lovelace"})
        edge = await graph.add_edge(
            source["id"], entity["id"], "mentions",
            {"source_id": "source-graph-1"},
        )
        assert await graph.get_node(source["id"]) is not None
        assert await graph.get_node(entity["id"]) is not None

        removed = await graph.reclaim_source("source-graph-1")
        assert removed == 2
        assert await graph.get_node(source["id"]) is None
        assert await graph.get_node(entity["id"]) is not None
        assert await graph.query_edges() == []
        await graph.close()

    asyncio.run(exercise())



def test_reclamation_persists_deletion_attribution_and_honors_retention_deadline(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'reclamation-retention.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    queue = SqliteReclamationStore(database_url)

    async def exercise():
        await queue.initialize()
        requested_at = datetime.now(timezone.utc)
        retain_until = requested_at.replace(year=requested_at.year + 1)
        job = await queue.enqueue(
            "retained-source", actor="human:scot", reason="retention test",
            requested_at=requested_at, retain_until=retain_until,
        )
        assert job["actor"] == "human:scot"
        assert job["reason"] == "retention test"
        assert job["retain_until"] is not None
        assert job["available_at"] is not None
        assert await queue.claim_due(10) == []

        with sqlite3.connect(tmp_path / "reclamation-retention.db") as connection:
            connection.execute(
                "UPDATE ensemble_source_reclamation SET available_at = ? WHERE source_id = ?",
                ("2000-01-01 00:00:00", "retained-source"),
            )
        claimed = await queue.claim_due(10)
        assert [item["source_id"] for item in claimed] == ["retained-source"]
        assert claimed[0]["actor"] == "human:scot"
        assert claimed[0]["reason"] == "retention test"
        await queue.close()

    asyncio.run(exercise())



def test_sqlite_deletion_audit_is_append_only_and_survives_restart(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'deletion-audit.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    async def exercise():
        first = SqliteReclamationStore(database_url)
        await first.initialize()
        await first.record_audit(
            "audited-source", "strategy_result", actor="human:owner",
            reason="requested erasure", details={"state": "queued"},
        )
        await first.record_audit(
            "audited-source", "completed", actor="human:owner",
            reason="requested erasure", details={"purged": True},
        )
        await first.close()

        second = SqliteReclamationStore(database_url)
        await second.initialize()
        events = await second.audit_events("audited-source")
        assert [item["event"] for item in events] == ["completed", "strategy_result"]
        assert events[0]["actor"] == "human:owner"
        assert events[0]["details"] == {"purged": True}
        await second.close()

    asyncio.run(exercise())
