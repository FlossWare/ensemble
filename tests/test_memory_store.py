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
        event_record = (await reopened.read_entries("session"))[0]
        assert event_record["result"] == "persisted"
        assert len(event_record["payload_sha256"]) == 64
        assert event_record["captured_at"].endswith("Z")
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



def test_memory_hybrid_uses_shared_configured_rrf_constant(monkeypatch):
    from ensemble.memory_store import _fuse_memory_results

    monkeypatch.setenv("ENSEMBLE_RRF_K", "20")
    results = _fuse_memory_results(
        [{"file": "memory-a", "section": "Intro", "score": 0.9}],
        [{"file": "memory-a", "section": "Intro", "score": 0.2}],
        5,
    )
    assert results[0]["fusion"]["k"] == 20
    assert results[0]["score"] == round((1 / 21) + (1 / 21), 6)
    assert results[0]["fusion"]["contributions"] == {
        "keyword": round(1 / 21, 6),
        "semantic": round(1 / 21, 6),
    }


def test_semantic_search_reuses_cached_chunks_and_vectors_until_content_changes(monkeypatch):
    import ensemble.memory_store as memory_module
    from ensemble.memory_store import InMemoryMemoryStore

    async def exercise():
        memory_module._memory_search_index.clear()
        store = InMemoryMemoryStore()
        await store.write("notes", "# Retrieval\\nVectorized content remains stable across repeated searches.")
        original = memory_module._vectorize_text
        calls = []

        def counted(value):
            calls.append(value)
            return original(value)

        monkeypatch.setattr(memory_module, "_vectorize_text", counted)
        first = await store.search_semantic("retrieval")
        second = await store.search_semantic("retrieval")
        assert first and second
        # One query vector per search, but the document chunk is vectorized once.
        assert calls.count("# Retrieval\\nVectorized content remains stable across repeated searches.") == 1

        await store.write("notes", "# Retrieval\\nUpdated content has a new vector representation.")
        updated = await store.search_semantic("updated")
        assert updated and "Updated content" in updated[0]["content"]
        assert calls.count("# Retrieval\\nUpdated content has a new vector representation.") == 1
        memory_module._memory_search_index.clear()

    asyncio.run(exercise())


def test_memory_search_index_is_bounded_and_skips_oversized_documents():
    from ensemble.memory_store import _MemorySearchIndex

    index = _MemorySearchIndex(max_bytes=8, max_document_bytes=8, max_entries=1)
    first = index.get("one", "alpha")
    assert first[0]["vector"]
    assert index._cached_bytes == 5
    second = index.get("two", "bravo")
    assert second[0]["vector"]
    assert index._cached_bytes <= 8
    assert len(index._cache) == 1
    oversized = index.get("large", "0123456789")
    assert oversized[0]["vector"]
    assert "large" not in [name for name, _ in index._cache]


def test_memory_search_index_builds_each_concurrent_cold_document_once(monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    import ensemble.memory_store as memory_module
    from ensemble.memory_store import _MemorySearchIndex

    index = _MemorySearchIndex()
    original = memory_module._vectorize_text
    calls = []
    calls_lock = threading.Lock()

    def counted(value):
        with calls_lock:
            calls.append(value)
        time.sleep(0.01)
        return original(value)

    monkeypatch.setattr(memory_module, "_vectorize_text", counted)
    content = "# Shared\\nConcurrent cold searches should share one index build."
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: index.get("shared", content), range(8)))
    assert all(result is results[0] for result in results)
    assert calls == [content]
