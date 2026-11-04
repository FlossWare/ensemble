import asyncio

from alembic import command
from alembic.config import Config

from ensemble.index_store import SqliteIndexStore
from ensemble.knowledge_store import SqliteKnowledgeStore
from ensemble.memory_store import InMemoryMemoryStore
from ensemble.models import Chunk, Document
from ensemble.reclamation import SqliteReclamationStore
from ensemble.store import SqlDocumentStore
from ensemble.vector_store import SqliteVectorStore


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

        await index.mark_purged("source-1")
        try:
            await index.restore_source("source-1", 5)
            raise AssertionError("permanently purged source was restored")
        except RuntimeError:
            pass

        assert await documents.reclaim_source("source-1") == 1
        assert await knowledge.reclaim_source("source-1") == 1
        assert await vectors.reclaim_source("source-1") == 1
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
