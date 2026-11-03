import asyncio
import hashlib
import json


from fastapi import FastAPI
from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.index_store import SqliteIndexStore
from ensemble.partitioning import (
    RequestScope, _ensure_partition_schema, partition_database_url,
    partition_scope_middleware, reset_request_scope, set_request_scope,
)


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


def test_partitioned_index_uses_separate_sqlite_databases(tmp_path):
    async def exercise():
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

    asyncio.run(exercise())


def test_document_ingestion_and_retrieval_do_not_cross_partitions(tmp_path, monkeypatch):
    alpha_token = "alpha-integration-token"
    beta_token = "beta-integration-token"
    token_map = {
        hashlib.sha256(alpha_token.encode()).hexdigest(): {
            "subject": "alpha-client", "partitions": ["alpha"]
        },
        hashlib.sha256(beta_token.encode()).hexdigest(): {
            "subject": "beta-client", "partitions": ["beta"]
        },
    }
    monkeypatch.setenv("ENSEMBLE_PARTITION_TOKENS", json.dumps(token_map))
    monkeypatch.setenv("ENSEMBLE_AUTH_REQUIRED", "1")
    base = f"sqlite+aiosqlite:///{tmp_path / 'ensemble.db'}"
    asyncio.run(asyncio.to_thread(_ensure_partition_schema, base, "default"))
    app = create_app(database_url=base)

    with TestClient(app) as client:
        alpha_headers = {
            "Authorization": f"Bearer {alpha_token}",
            "X-Ensemble-Partition": "alpha",
        }
        beta_headers = {
            "Authorization": f"Bearer {beta_token}",
            "X-Ensemble-Partition": "beta",
        }
        alpha_ingest = client.post(
            "/api/v1/ingest/documents",
            headers=alpha_headers,
            json={
                "source_id": "shared-source",
                "revision": 1,
                "title": "Alpha document",
                "content": "alpha-only partition secret phrase",
            },
        )
        beta_ingest = client.post(
            "/api/v1/ingest/documents",
            headers=beta_headers,
            json={
                "source_id": "shared-source",
                "revision": 1,
                "title": "Beta document",
                "content": "beta-only partition secret phrase",
            },
        )
        assert alpha_ingest.status_code == 201, alpha_ingest.text
        assert beta_ingest.status_code == 201, beta_ingest.text

        alpha_search = client.post(
            "/api/v1/search",
            headers=alpha_headers,
            json={"query": "alpha-only partition secret phrase", "limit": 10},
        )
        beta_search = client.post(
            "/api/v1/search",
            headers=beta_headers,
            json={"query": "beta-only partition secret phrase", "limit": 10},
        )
        assert alpha_search.status_code == 200, alpha_search.text
        assert beta_search.status_code == 200, beta_search.text
        assert any("alpha-only" in item["text"] for item in alpha_search.json())
        assert not any("beta-only" in item["text"] for item in alpha_search.json())
        assert any("beta-only" in item["text"] for item in beta_search.json())
        assert not any("alpha-only" in item["text"] for item in beta_search.json())

        denied = client.post(
            "/api/v1/search",
            headers={**alpha_headers, "X-Ensemble-Partition": "beta"},
            json={"query": "beta-only", "limit": 10},
        )
        assert denied.status_code == 403
