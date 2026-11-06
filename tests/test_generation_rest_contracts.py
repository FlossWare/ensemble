import asyncio

from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.index_memory import InMemoryIndexStore
from ensemble.store import InMemoryDocumentStore


def test_index_generation_source_copy_uses_internal_rest_contracts():
    index_store = InMemoryIndexStore()
    asyncio.run(index_store.record_revision("source-a", 1, "revision-a", {"profile": "v1"}))
    asyncio.run(index_store.record_revision("source-b", 1, "revision-b", {"profile": "v1"}))
    asyncio.run(index_store.create_generation("generation-rest-copy"))

    app = create_app(store=InMemoryDocumentStore(), index_store=index_store)
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/index/generations/generation-rest-copy/copy/index",
            json={"page_size": 1},
        )
        assert first.status_code == 200
        assert first.json()["status"] == "in_progress"
        assert first.json()["page_count"] == 1

        second = client.post(
            "/api/v1/index/generations/generation-rest-copy/copy/index",
            json={"page_size": 1},
        )
        assert second.status_code == 200
        assert second.json()["status"] == "complete"
        assert second.json()["source_count"] == 2

        status = client.get("/api/v1/index/generations")
        generation = next(
            item for item in status.json()["generations"]
            if item["generation_id"] == "generation-rest-copy"
        )
        assert generation["checkpoints"]["index"]["sealed"] is True

    copied_a = asyncio.run(index_store.get_generation_source("generation-rest-copy", "source-a"))
    copied_b = asyncio.run(index_store.get_generation_source("generation-rest-copy", "source-b"))
    assert copied_a["revision_id"] == "revision-a"
    assert copied_b["revision_id"] == "revision-b"
