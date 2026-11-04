import asyncio
import hashlib

from ensemble.memory_store import SqliteMemoryStore, memory_metadata


def test_sqlite_memory_knowledge_and_event_records_survive_restart(tmp_path):
    database = tmp_path / "memory.sqlite"
    url = f"sqlite+aiosqlite:///{database}"
    content = "# Retrieval\nSQLite stores source provenance and search content."
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

    async def exercise():
        store = SqliteMemoryStore(url)
        async with store.engine.begin() as connection:
            await connection.run_sync(memory_metadata.create_all)
        await store.initialize()
        await store.append("session", {"result": "persisted"}, event_id="event-1")
        await store.ingest_claude_markdown(
            "/repo/docs/retrieval.md", content, digest, {"scope": "/repo"}
        )
        assert (await store.search_semantic("source provenance", source="claude-code"))[0]["source"] == "claude-code"
        await store.close()

        reopened = SqliteMemoryStore(url)
        await reopened.initialize()
        assert (await reopened.read_entries("session"))[0]["result"] == "persisted"
        assert (await reopened.knowledge_status())["indexed_document_count"] == 1
        assert (await reopened.append("session", {"result": "persisted"}, event_id="event-1"))["status"] == "duplicate"
        await reopened.reconcile_claude_markdown("claude-code", "/repo", [])
        assert (await reopened.knowledge_status())["indexed_document_count"] == 0
        assert await reopened.search_semantic("source provenance", source="claude-code") == []
        await reopened.close()

    asyncio.run(exercise())
