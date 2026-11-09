from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.store import InMemoryDocumentStore
from ensemble.ledger_store import JsonlAppendOnlyLedger


def event(event_id, payload=None):
    return {
        "event_id": event_id,
        "event_type": "test.event",
        "source": "test",
        "timestamp": "2026-11-09T00:00:00Z",
        "correlation_id": "ledger-api-test",
        "payload": payload if payload is not None else {"id": event_id},
    }


def test_ledger_rest_append_pull_lookup_verify_and_import(tmp_path):
    ledger = JsonlAppendOnlyLedger(tmp_path / "ledger" / "events.jsonl")
    app = create_app(store=InMemoryDocumentStore(), ledger_store=ledger)
    with TestClient(app) as client:
        first = client.post("/api/v1/ledger/entries", json={"entry": event("one")})
        assert first.status_code == 200
        assert first.json()["entry"]["sequence"] == 1

        duplicate = client.post("/api/v1/ledger/entries", json={"entry": event("one")})
        assert duplicate.status_code == 200
        assert duplicate.json()["status"] == "duplicate"

        conflict = client.post(
            "/api/v1/ledger/entries",
            json={"entry": event("one", {"different": True})},
        )
        assert conflict.status_code == 409

        status = client.get("/api/v1/ledger/status")
        assert status.status_code == 200
        assert status.json()["head_sequence"] == 1

        page = client.get("/api/v1/ledger/entries", params={"after_sequence": 0, "limit": 1})
        assert page.status_code == 200
        assert [item["event_id"] for item in page.json()["entries"]] == ["one"]

        lookup = client.get("/api/v1/ledger/entries/one")
        assert lookup.status_code == 200
        assert lookup.json()["entry"]["event_id"] == "one"

        verified = client.post("/api/v1/ledger/verify", json={})
        assert verified.status_code == 200
        assert verified.json()["ok"] is True

        imported = client.post(
            "/api/v1/ledger/import",
            json={"source_ledger_id": "source-ledger", "entries": [event("two")]},
        )
        assert imported.status_code == 200
        assert imported.json()["imported"] == ["two"]
