import asyncio

from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.index_memory import InMemoryIndexStore
from ensemble.store import InMemoryDocumentStore


def test_source_restore_delegates_capability_work_through_rest():
    index_store = InMemoryIndexStore()
    asyncio.run(index_store.record_revision(
        "restore-source", 1, "restore-revision-1", {"profile": "v1"},
    ))
    app = create_app(store=InMemoryDocumentStore(), index_store=index_store)

    with TestClient(app) as client:
        tombstoned = client.post("/api/v1/index/tombstones", json={
            "source_id": "restore-source",
            "revision_sequence": 2,
            "reason": "test lifecycle",
        })
        assert tombstoned.status_code == 200
        assert tombstoned.json()["decision"] == "tombstoned"

        restored = client.post("/api/v1/index/restorations", json={
            "source_id": "restore-source",
            "revision_sequence": 3,
        })
        assert restored.status_code == 200
        assert restored.json()["source"]["tombstoned"] is False
        assert restored.json()["reclamation"] == "cancelled"

        source = client.get("/api/v1/index/sources/restore-source")
        assert source.status_code == 200
        assert source.json()["source"]["high_sequence"] == 3
        assert source.json()["source"]["tombstoned"] is False
