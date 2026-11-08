import asyncio
import hashlib
import json


from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx

from ensemble.api import create_app
from ensemble.graph_store import SqliteGraphStore
from ensemble.index_store import SqliteIndexStore
from ensemble.knowledge_store import SqliteKnowledgeStore
from ensemble.memory_store import SqliteMemoryStore
from ensemble.vector_store import SqliteVectorStore
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
    allowed = client.get("/api/v1/scope", headers={"Authorization": f"Bearer {credential}", "X-Ensemble-Partition": "alpha", "X-Correlation-ID": "trace-123"})
    assert allowed.status_code == 200
    assert allowed.json() == {"subject": "integration-test", "partition": "alpha"}
    assert allowed.headers["x-correlation-id"] == "trace-123"
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

    client = TestClient(app)
    assert client.get("/api/v1/scope").status_code == 200
    response = client.get("/api/v1/scope", headers={"X-Ensemble-Partition": "untrusted"})
    assert response.status_code == 403


def test_remote_api_uses_default_partition_without_credentials_in_local_mode(monkeypatch):
    monkeypatch.delenv("ENSEMBLE_PARTITION_TOKENS", raising=False)
    monkeypatch.setenv("ENSEMBLE_AUTH_REQUIRED", "0")
    app = FastAPI()
    app.middleware("http")(partition_scope_middleware)

    @app.get("/api/v1/scope")
    async def scope():
        return {"ok": True}

    async def request_remote():
        transport = httpx.ASGITransport(app=app, client=("203.0.113.25", 4567))
        async with httpx.AsyncClient(transport=transport, base_url="http://ensemble") as client:
            return await client.get("/api/v1/scope")

    response = asyncio.run(request_remote())
    assert response.status_code == 200


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
        schema = client.get("/openapi.json").json()
        assert schema["components"]["securitySchemes"]["BearerAuth"]["scheme"] == "bearer"
        assert schema["paths"]["/api/v1/search"]["post"]["security"] == [{"BearerAuth": []}]
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


def test_memory_knowledge_graph_and_vector_stores_are_partition_isolated(tmp_path):
    async def exercise():
        base = f"sqlite+aiosqlite:///{tmp_path / 'capabilities.db'}"
        await asyncio.to_thread(_ensure_partition_schema, base, "default")
        knowledge = SqliteKnowledgeStore(base)
        graph = SqliteGraphStore(base)
        vector = SqliteVectorStore(base)
        memory = SqliteMemoryStore(base)
        stores = [knowledge, graph, vector, memory]
        try:
            for store in stores:
                await store.initialize()

            token = set_request_scope(RequestScope(partition_id="workspace-a"))
            await knowledge.put({
                "source_id": "same-source", "revision_sequence": 1,
                "revision_id": "alpha-revision", "title": "Alpha",
                "content": "alpha knowledge", "origin": "test",
                "locator": "alpha://source", "profiles": {},
            })
            await graph.add_node("test", {"value": "alpha"}, node_id="same-node")
            await vector.upsert("shared-collection", "test-model", 2, [{
                "id": "same-record", "embedding": [1.0, 0.0],
                "document": "alpha vector", "metadata": {"partition": "alpha"},
            }])
            await memory.write("shared-memory", "alpha memory")
            reset_request_scope(token)

            token = set_request_scope(RequestScope(partition_id="workspace-b"))
            assert await knowledge.get("same-source") is None
            assert await graph.get_node("same-node") is None
            assert await vector.existing("shared-collection", ["same-record"]) == []
            assert await memory.read("shared-memory") is None
            await knowledge.put({
                "source_id": "same-source", "revision_sequence": 1,
                "revision_id": "beta-revision", "title": "Beta",
                "content": "beta knowledge", "origin": "test",
                "locator": "beta://source", "profiles": {},
            })
            await graph.add_node("test", {"value": "beta"}, node_id="same-node")
            await vector.upsert("shared-collection", "test-model", 2, [{
                "id": "same-record", "embedding": [0.0, 1.0],
                "document": "beta vector", "metadata": {"partition": "beta"},
            }])
            await memory.write("shared-memory", "beta memory")
            assert (await knowledge.get("same-source"))["revision_id"] == "beta-revision"
            assert (await graph.get_node("same-node"))["properties"]["value"] == "beta"
            assert await vector.existing("shared-collection", ["same-record"]) == ["same-record"]
            assert await memory.read("shared-memory") == "beta memory"
            reset_request_scope(token)

            token = set_request_scope(RequestScope(partition_id="workspace-a"))
            assert (await knowledge.get("same-source"))["revision_id"] == "alpha-revision"
            assert (await graph.get_node("same-node"))["properties"]["value"] == "alpha"
            assert await vector.existing("shared-collection", ["same-record"]) == ["same-record"]
            assert await memory.read("shared-memory") == "alpha memory"
            reset_request_scope(token)
        finally:
            for store in stores:
                await store.close()

    asyncio.run(exercise())


def test_tombstones_and_replayed_revisions_are_partition_local(tmp_path):
    async def exercise():
        base = f"sqlite+aiosqlite:///{tmp_path / 'tombstones.db'}"
        await asyncio.to_thread(_ensure_partition_schema, base, "default")
        store = SqliteIndexStore(base)
        try:
            token = set_request_scope(RequestScope(partition_id="workspace-a"))
            try:
                first = await store.record_revision("shared-source", 1, "alpha-r1", {})
                assert first["decision"] == "accepted"
                deleted = await store.record_tombstone("shared-source", 2)
                assert deleted["decision"] == "tombstoned"

                replay = await store.record_revision("shared-source", 1, "alpha-r1", {})
                late = await store.record_revision("shared-source", 3, "alpha-r3", {})
                assert replay["decision"] == "tombstoned"
                assert late["decision"] == "tombstoned"
                assert (await store.get_source("shared-source"))["tombstoned"] is True
            finally:
                reset_request_scope(token)

            token = set_request_scope(RequestScope(partition_id="workspace-b"))
            try:
                independent = await store.record_revision("shared-source", 1, "beta-r1", {})
                assert independent["decision"] == "accepted"
                source = await store.get_source("shared-source")
                assert source["revision_id"] == "beta-r1"
                assert source["tombstoned"] is False
            finally:
                reset_request_scope(token)

            token = set_request_scope(RequestScope(partition_id="default"))
            try:
                assert await store.get_source("shared-source") is None
            finally:
                reset_request_scope(token)
        finally:
            await store.close()

    asyncio.run(exercise())
