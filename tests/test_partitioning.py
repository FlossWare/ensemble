import hashlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ensemble.partitioning import partition_database_url, partition_scope_middleware


def test_partition_database_urls_are_stable_and_separate(tmp_path):
    base = f"sqlite+aiosqlite:///{tmp_path / 'ensemble.db'}"
    first = partition_database_url(base, "workspace-a")
    second = partition_database_url(base, "workspace-b")
    assert first != second
    assert first == partition_database_url(base, "workspace-a")
    assert "workspace-a" not in first


def test_partition_token_authorizes_only_assigned_partitions(monkeypatch):
    credential = "test-only-secret"
    digest = hashlib.sha256(credential.encode()).hexdigest()
    monkeypatch.setenv("ENSEMBLE_PARTITION_TOKENS", json.dumps({digest: {"subject": "integration-test", "partitions": ["alpha", "beta"]}}))
    monkeypatch.setenv("ENSEMBLE_AUTH_REQUIRED", "1")
    app = FastAPI()
    app.middleware("http")(partition_scope_middleware)

    @app.get("/api/v1/scope")
    async def scope():
        from ensemble.partitioning import current_request_scope
        current = current_request_scope()
        return {"subject": current.principal_id, "partition": current.partition_id}

    client = TestClient(app)
    assert client.get("/api/v1/scope").status_code == 401
    allowed = client.get("/api/v1/scope", headers={"Authorization": f"Bearer {credential}", "X-Ensemble-Partition": "alpha"})
    assert allowed.status_code == 200
    assert allowed.json() == {"subject": "integration-test", "partition": "alpha"}
    denied = client.get("/api/v1/scope", headers={"Authorization": f"Bearer {credential}", "X-Ensemble-Partition": "gamma"})
    assert denied.status_code == 403


def test_local_mode_cannot_select_another_partition(monkeypatch):
    monkeypatch.delenv("ENSEMBLE_PARTITION_TOKENS", raising=False)
    monkeypatch.setenv("ENSEMBLE_AUTH_REQUIRED", "0")
    app = FastAPI()
    app.middleware("http")(partition_scope_middleware)

    @app.get("/api/v1/scope")
    async def scope():
        from ensemble.partitioning import current_request_scope
        return {"partition": current_request_scope().partition_id}

    response = TestClient(app).get("/api/v1/scope", headers={"X-Ensemble-Partition": "untrusted"})
    assert response.status_code == 403
