import asyncio

from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.index_memory import InMemoryIndexStore
from ensemble.store import InMemoryDocumentStore


def test_permanent_reclamation_delegates_to_capability_rest():
    index_store = InMemoryIndexStore()
    asyncio.run(index_store.record_revision(
        "purge-source", 1, "purge-revision-1", {"profile": "v1"},
    ))
    app = create_app(
        store=InMemoryDocumentStore(),
        index_store=index_store,
        run_reclamation_worker=False,
    )

    with TestClient(app) as client:
        tombstoned = client.post("/api/v1/index/tombstones", json={
            "source_id": "purge-source",
            "revision_sequence": 2,
            "reason": "permanent purge contract test",
        })
        assert tombstoned.status_code == 200
        assert tombstoned.json()["decision"] == "tombstoned"

        reclaimed = client.post("/api/v1/index/reclamation/run")
        assert reclaimed.status_code == 200
        assert reclaimed.json()["processed"] == 1

        source = client.get("/api/v1/index/sources/purge-source")
        assert source.status_code == 200
        assert source.json()["source"]["tombstoned"] is True
        assert source.json()["source"]["purged"] is True

        jobs = client.get("/api/v1/index/reclamation")
        job = next(item for item in jobs.json()["jobs"] if item["source_id"] == "purge-source")
        assert job["state"] == "complete"
