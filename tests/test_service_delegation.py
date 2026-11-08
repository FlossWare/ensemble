from __future__ import annotations

import uuid

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from ensemble.api import create_app


def _client(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'ensemble.db'}"
    migration_config = Config("alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")
    return TestClient(create_app(database_url=database_url))


def test_provider_executes_direct_search_service_without_creating_task(tmp_path):
    with _client(tmp_path) as client:
        ingested = client.post("/api/v1/ingest/documents", json={
            "source_id": "peer-service-source",
            "revision": 1,
            "title": "Peer service",
            "content": "Direct service delegation invokes a bounded capability and returns a response.",
        })
        assert ingested.status_code == 201

        request_id = str(uuid.uuid4())
        response = client.post("/federation/services", json={
            "request_id": request_id,
            "requester_instance_id": "requester-instance",
            "target_capability": "search",
            "operation": "search.retrieve",
            "payload": {"query": "bounded capability", "limit": 5},
        })

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["request_id"] == request_id
        assert body["provider_instance_id"] == client.app.state.federation_instance["instance_id"]
        assert body["operation"] == "search.retrieve"
        assert body["result"]["results"]
        assert body["result"]["results"][0]["source_id"] == "peer-service-source"
        assert client.get("/federation/tasks").json()["tasks"] == []


def test_requester_invokes_peer_service_with_direct_request_response(tmp_path, monkeypatch):
    from ensemble import api as api_module

    monkeypatch.setenv("ENSEMBLE_PERSISTENCE_ROOT", str(tmp_path))
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "contract_version": 1,
                "request_id": captured["body"]["request_id"],
                "provider_instance_id": "remote-peer",
                "operation": "search.retrieve",
                "result": {"results": [], "retrieval": "lexical"},
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json):
            captured["url"] = url
            captured["body"] = json
            return FakeResponse()

    monkeypatch.setattr(api_module.httpx, "AsyncClient", FakeClient)
    with _client(tmp_path) as client:
        client.app.state.federation_registry.observe({
            "kind": "ensemble.presence",
            "protocol_version": "1",
            "instance_id": "remote-peer",
            "name": "remote-server",
            "endpoint": "http://192.0.2.20:8090",
            "capabilities": ["search"],
        })
        response = client.post("/federation/peers/remote-peer/services", json={
            "request_id": str(uuid.uuid4()),
            "target_capability": "search",
            "operation": "search.retrieve",
            "payload": {"query": "direct service", "limit": 3},
        })

        assert response.status_code == 200, response.text
        assert captured["url"] == "http://192.0.2.20:8090/federation/services"
        assert captured["body"]["requester_instance_id"] == client.app.state.federation_instance["instance_id"]
        assert captured["body"]["operation"] == "search.retrieve"
        assert captured["timeout"] == 15.0
        assert response.json()["provider_instance_id"] == "remote-peer"


def test_service_request_rejects_expired_deadline(tmp_path):
    with _client(tmp_path) as client:
        response = client.post("/federation/services", json={
            "requester_instance_id": "requester-instance",
            "target_capability": "search",
            "operation": "search.retrieve",
            "deadline": "2000-01-01T00:00:00Z",
            "payload": {"query": "expired", "limit": 1},
        })
        assert response.status_code == 408
