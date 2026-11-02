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


def test_sqlite_memory_expiry_and_delete_semantics(tmp_path):
    from datetime import datetime, timedelta, timezone

    database = tmp_path / "memory-lifecycle.sqlite"
    store = SqliteMemoryStore(f"sqlite+aiosqlite:///{database}")

    async def exercise():
        async with store.engine.begin() as connection:
            await connection.run_sync(memory_metadata.create_all)
        await store.initialize()
        now = datetime.now(timezone.utc)
        context = {
            "request_id": "request-1",
            "execution_id": "current",
            "objective": "test expiry",
            "lineage": [],
        }
        for execution_id, result, expires_at in (
            ("expired", "expired event", (now - timedelta(minutes=1)).isoformat()),
            ("active", "active event", (now + timedelta(hours=1)).isoformat()),
        ):
            await store.append("execution", {
                "result": result,
                "expires_at": expires_at,
                "execution_context": {
                    "request_id": "request-1", "execution_id": execution_id,
                    "objective": "prior", "lineage": [],
                },
            })
        recalled = await store.retrieve("execution", context)
        assert [item["record"]["result"] for item in recalled] == ["active event"]
        assert len(await store.read_entries("execution")) == 2

        await store.write("notes", "content to delete")
        await store.append("notes", {"result": "event to delete"}, event_id="delete-event")
        deleted = await store.delete("notes")
        assert deleted == {
            "status": "deleted", "memory_deleted": True,
            "entries_deleted": 1, "sources_deleted": 0,
        }
        assert await store.read("notes") is None
        assert await store.read_entries("notes") == []
        assert (await store.delete("notes"))["status"] == "not_found"

        source_content = "# Source\\nImported memory content."
        source_digest = hashlib.sha256(source_content.encode("utf-8")).hexdigest()
        imported = await store.ingest_claude_markdown(
            "/repo/docs/source.md", source_content, source_digest, {"scope": "/repo"}
        )
        source_delete = await store.delete(imported["document"])
        assert source_delete["status"] == "deleted"
        assert source_delete["sources_deleted"] == 1
        assert source_delete["memory_deleted"] is True
        assert (await store.knowledge_status())["indexed_document_count"] == 0
        await store.close()

    asyncio.run(exercise())
