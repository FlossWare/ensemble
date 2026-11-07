from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.index_memory import InMemoryIndexStore
from ensemble.store import InMemoryDocumentStore


def test_index_generation_coordination_is_available_over_rest():
    app = create_app(
        store=InMemoryDocumentStore(),
        index_store=InMemoryIndexStore(),
    )

    with TestClient(app) as client:
        ready = client.get("/api/v1/index/generation-writes/ready")
        assert ready.status_code == 200
        assert ready.json() == {"ok": True, "ready": True}

        revision = client.post("/api/v1/index/revisions", json={
            "source_id": "rest-ack-source",
            "revision_sequence": 1,
            "revision_id": "revision-one",
            "profiles": {"chunking": "window-v1", "media_type": "text/plain"},
        })
        assert revision.status_code == 200
        assert revision.json()["decision"] == "accepted"

        acknowledged = client.post(
            "/api/v1/index/generation-changes/acknowledgements",
            json={
                "source_id": "rest-ack-source",
                "revision_sequence": 1,
                "tombstoned": False,
            },
        )
        assert acknowledged.status_code == 200
        assert acknowledged.json() == {
            "ok": True,
            "source_id": "rest-ack-source",
            "revision_sequence": 1,
            "tombstoned": False,
        }


def test_index_generation_acknowledgement_rejects_invalid_payload():
    app = create_app(store=InMemoryDocumentStore(), index_store=InMemoryIndexStore())

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/index/generation-changes/acknowledgements",
            json={
                "source_id": "rest-ack-source",
                "revision_sequence": True,
                "tombstoned": "false",
            },
        )

    assert response.status_code == 400
