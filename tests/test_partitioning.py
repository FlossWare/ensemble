import asyncio
import hashlib
import json

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ensemble.index_store import SqliteIndexStore
from ensemble.partitioning import (\n    RequestScope, _ensure_partition_schema, partition_database_url,\n    partition_scope_middleware, reset_request_scope, set_request_scope,\n)


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


@pytest.mark.asyncio
async def test_partitioned_index_uses_separate_sqlite_databases(tmp_path):
    base = f"sqlite+aiosqlite:///{tmp_path / 'ensemble.db'}"
    await asyncio.to_thread(_ensure_partition_schema, base, "default")
    store = SqliteIndexStore(base)
    try:
        token = set_request_scope(RequestScope(partition_id="default"))
        await store.record_revision("same-source", 1, "default-revision", {})
        reset_request_scope(token)

        token = set_request_scope(RequestScope(partition_id="workspace-a"))
        await store.record_revision("same-source", 1, "workspace-a-revision", {})
        assert (await store.get_source("same-source"))["revision_id"] == "workspace-a-revision"
        reset_request_scope(token)

        token = set_request_scope(RequestScope(partition_id="workspace-b"))
        assert await store.get_source("same-source") is None
        await store.record_revision("same-source", 1, "workspace-b-revision", {})
        assert (await store.get_source("same-source"))["revision_id"] == "workspace-b-revision"
        reset_request_scope(token)

        token = set_request_scope(RequestScope(partition_id="default"))
        assert (await store.get_source("same-source"))["revision_id"] == "default-revision"
        reset_request_scope(token)
    finally:
        await store.close()
