import asyncio

from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.index_memory import InMemoryIndexStore
from ensemble.knowledge_store import InMemoryKnowledgeStore
from ensemble.store import InMemoryDocumentStore


def test_index_generation_source_copy_uses_internal_rest_contracts():
    index_store = InMemoryIndexStore()
    knowledge_store = InMemoryKnowledgeStore()
    asyncio.run(knowledge_store.put({
        "source_id": "source-a", "revision_sequence": 1, "revision_id": "revision-a",
        "title": "Source A", "content": "knowledge generation fixture",
        "origin": "test", "locator": "source-a", "profiles": {"profile": "v1"},
    }))
    asyncio.run(index_store.record_revision("source-a", 1, "revision-a", {"profile": "v1"}))
    asyncio.run(index_store.record_revision("source-b", 1, "revision-b", {"profile": "v1"}))
    asyncio.run(index_store.acknowledge_generation_change("source-a", 1, False))
    asyncio.run(index_store.acknowledge_generation_change("source-b", 1, False))
    asyncio.run(index_store.create_generation("generation-rest-copy"))

    app = create_app(store=InMemoryDocumentStore(), index_store=index_store, knowledge_store=knowledge_store)
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

        documents = client.post(
            "/api/v1/index/generations/generation-rest-copy/copy/documents",
            json={"page_size": 1},
        )
        assert documents.status_code == 200
        assert documents.json()["status"] == "in_progress"
        documents = client.post(
            "/api/v1/index/generations/generation-rest-copy/copy/documents",
            json={"page_size": 1},
        )
        assert documents.status_code == 200
        assert documents.json()["status"] == "complete"
        assert documents.json()["source_count"] == 2

        vectors = client.post(
            "/api/v1/index/generations/generation-rest-copy/copy/vectors",
            json={"page_size": 10},
        )
        assert vectors.status_code == 200
        assert vectors.json()["status"] == "complete"

        memory = client.put(
            "/api/v1/memory/memories/generation-fixture",
            json={"content": "generation snapshot fixture"},
        )
        assert memory.status_code == 200
        copied_memory = client.post(
            "/api/v1/index/generations/generation-rest-copy/copy/memory",
            json={"page_size": 10},
        )
        assert copied_memory.status_code == 200
        assert copied_memory.json()["status"] == "complete"
        assert copied_memory.json()["record_count"] == 1

        knowledge = client.post(
            "/api/v1/index/generations/generation-rest-copy/copy/knowledge",
            json={"page_size": 10},
        )
        assert knowledge.status_code == 200
        assert knowledge.json()["status"] == "complete"
        assert knowledge.json()["record_count"] == 1

        changed = client.post("/api/v1/index/revisions", json={
            "source_id": "source-a",
            "revision_sequence": 2,
            "revision_id": "revision-a2",
            "profiles": {"profile": "v2"},
        })
        assert changed.status_code == 200
        acknowledged = client.post("/api/v1/index/generation-changes/acknowledgements", json={
            "source_id": "source-a",
            "revision_sequence": 2,
            "tombstoned": False,
        })
        assert acknowledged.status_code == 200

        reconciled = client.post(
            "/api/v1/index/generations/generation-rest-copy/reconcile/index",
            json={"page_size": 10},
        )
        assert reconciled.status_code == 200
        assert reconciled.json()["status"] == "caught_up"
        assert reconciled.json()["change_count"] == 1

        status = client.get("/api/v1/index/generations")
        generation = next(
            item for item in status.json()["generations"]
            if item["generation_id"] == "generation-rest-copy"
        )
        assert generation["checkpoints"]["index"]["sealed"] is True
        assert generation["checkpoints"]["documents"]["sealed"] is True
        assert generation["checkpoints"]["vectors"]["sealed"] is True
        assert generation["checkpoints"]["memory"]["sealed"] is True
        assert generation["checkpoints"]["knowledge"]["sealed"] is True

    copied_a = asyncio.run(index_store.get_generation_source("generation-rest-copy", "source-a"))
    copied_b = asyncio.run(index_store.get_generation_source("generation-rest-copy", "source-b"))
    assert copied_a["revision_id"] == "revision-a2"
    assert copied_b["revision_id"] == "revision-b"
