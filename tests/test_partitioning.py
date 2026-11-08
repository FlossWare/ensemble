import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import update


from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx

from ensemble.api import create_app
from ensemble.crawl_store import SqliteCrawlJobStore, crawl_jobs
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


def test_remote_api_requires_credentials_in_local_mode(monkeypatch):
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
    assert response.status_code == 503


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

        # Hybrid composes retrieval, entity extraction, Graph expansion, and
        # Memory through internal REST calls. Those calls must retain the
        # authenticated request's partition rather than falling back to default.
        alpha_hybrid = client.post(
            "/api/v1/search/hybrid",
            headers=alpha_headers,
            json={"query": "alpha-only partition secret phrase", "limit": 10},
        )
        beta_hybrid = client.post(
            "/api/v1/search/hybrid",
            headers=beta_headers,
            json={"query": "beta-only partition secret phrase", "limit": 10},
        )
        assert alpha_hybrid.status_code == 200, alpha_hybrid.text
        assert beta_hybrid.status_code == 200, beta_hybrid.text
        assert "graph_expansion" in alpha_hybrid.json()
        assert "intent" in alpha_hybrid.json()
        assert alpha_hybrid.json()["graph_expansion"]["candidate_sources"] == 1
        assert alpha_hybrid.json()["graph_expansion"]["candidate_chunks"] >= 1
        assert beta_hybrid.json()["graph_expansion"]["candidate_sources"] == 1
        assert beta_hybrid.json()["graph_expansion"]["candidate_chunks"] >= 1
        assert any("alpha-only" in item["text"] for item in alpha_hybrid.json()["results"])
        assert not any("beta-only" in item["text"] for item in alpha_hybrid.json()["results"])
        assert any("beta-only" in item["text"] for item in beta_hybrid.json()["results"])
        assert not any("alpha-only" in item["text"] for item in beta_hybrid.json()["results"])

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


def test_startup_recovers_crawl_jobs_in_every_configured_partition(tmp_path, monkeypatch):
    credentials = {"alpha": "alpha-recovery-token", "beta": "beta-recovery-token"}
    token_map = {
        hashlib.sha256(token.encode()).hexdigest(): {
            "subject": f"{partition}-worker", "partitions": [partition],
        }
        for partition, token in credentials.items()
    }
    monkeypatch.setenv("ENSEMBLE_PARTITION_TOKENS", json.dumps(token_map))
    monkeypatch.setenv("ENSEMBLE_AUTH_REQUIRED", "1")
    base = f"sqlite+aiosqlite:///{tmp_path / 'partitioned-crawl-recovery.db'}"
    asyncio.run(asyncio.to_thread(_ensure_partition_schema, base, "default"))

    async def seed_stale_jobs():
        store = SqliteCrawlJobStore(base)
        job_ids = {}
        try:
            for partition in credentials:
                scope = set_request_scope(RequestScope(partition_id=partition))
                try:
                    job = await store.enqueue(
                        f"https://{partition}.example.com/", 2, 0,
                    )
                    await store.claim_next()
                    async with store.engine.begin() as connection:
                        await connection.execute(update(crawl_jobs).where(
                            crawl_jobs.c.job_id == job["job_id"],
                        ).values(
                            updated_at=datetime.now(timezone.utc) - timedelta(seconds=301),
                        ))
                    job_ids[partition] = job["job_id"]
                finally:
                    reset_request_scope(scope)
        finally:
            await store.close()
        return job_ids

    job_ids = asyncio.run(seed_stale_jobs())
    app = create_app(database_url=base)
    with TestClient(app):
        async def verify_recovery():
            store = SqliteCrawlJobStore(base)
            try:
                states = {}
                for partition, job_id in job_ids.items():
                    scope = set_request_scope(RequestScope(partition_id=partition))
                    try:
                        states[partition] = await store.get(job_id)
                    finally:
                        reset_request_scope(scope)
                return states
            finally:
                await store.close()

        recovered = asyncio.run(verify_recovery())

    assert {partition: job["status"] for partition, job in recovered.items()} == {
        "alpha": "interrupted", "beta": "interrupted",
    }



def test_hybrid_reranker_receives_only_authorized_partition_candidates(tmp_path, monkeypatch):
    class FixedEmbeddings:
        available = True

        def status(self):
            return {"ok": True, "ready": True, "model": "partition-test", "dimensions": 3}

        async def embed(self, texts, mode="document"):
            return [
                [0.0, 1.0, 0.0] if "graph expansion only" in text else [1.0, 0.0, 0.0]
                for text in texts
            ]

    class RecordingReranker:
        def __init__(self):
            self.calls = []

        @property
        def available(self):
            return True

        def status(self):
            return {"ok": True, "ready": True, "provider": "recording-test"}

        def score(self, query, documents):
            self.calls.append(list(documents))
            return [float(index) for index, _ in enumerate(documents)]

    alpha_token, beta_token = "alpha-rerank-secret", "beta-rerank-secret"
    token_map = {
        hashlib.sha256(alpha_token.encode()).hexdigest(): {
            "subject": "alpha-client", "partitions": ["alpha"],
        },
        hashlib.sha256(beta_token.encode()).hexdigest(): {
            "subject": "beta-client", "partitions": ["beta"],
        },
    }
    monkeypatch.setenv("ENSEMBLE_PARTITION_TOKENS", json.dumps(token_map))
    monkeypatch.setenv("ENSEMBLE_AUTH_REQUIRED", "1")
    base = f"sqlite+aiosqlite:///{tmp_path / 'reranker-partitions.db'}"
    asyncio.run(asyncio.to_thread(_ensure_partition_schema, base, "default"))
    reranker = RecordingReranker()
    app = create_app(
        database_url=base, reranker=reranker,
        embedding_provider=FixedEmbeddings(),
    )

    alpha_headers = {
        "Authorization": f"Bearer {alpha_token}",
        "X-Ensemble-Partition": "alpha",
    }
    beta_headers = {
        "Authorization": f"Bearer {beta_token}",
        "X-Ensemble-Partition": "beta",
    }

    with TestClient(app) as client:
        for headers, source_text, title in (
            (alpha_headers, "alpha confidential retrieval material", "Alpha"),
            (beta_headers, "beta confidential retrieval material", "Beta"),
        ):
            response = client.post(
                "/api/v1/ingest/documents",
                headers=headers,
                json={
                    "source_id": "same-source",
                    "revision": 1,
                    "title": title,
                    "content": source_text,
                },
            )
            assert response.status_code == 201, response.text

        # A graph-only source in each partition must not leak through expansion.
        # Its content does not match lexical search and its vector is orthogonal.
        for headers, marker in (
            (alpha_headers, "alpha graph expansion only source"),
            (beta_headers, "beta graph expansion only source"),
        ):
            ingested = client.post(
                "/api/v1/ingest/documents", headers=headers,
                json={"source_id": "graph-only-source", "revision": 1,
                      "title": marker, "content": marker},
            )
            assert ingested.status_code == 201, ingested.text
            entity = client.post(
                "/api/v1/graph/nodes", headers=headers,
                json={"id": "shared-path-entity", "type": "entity", "properties": {
                    "entity_type": "path", "normalized": "/workspace/secret.md",
                }},
            )
            source = client.post(
                "/api/v1/graph/nodes", headers=headers,
                json={"id": "shared-source-node", "type": "source", "properties": {
                    "source_id": "graph-only-source", "revision": 1,
                }},
            )
            assert entity.status_code == 200, entity.text
            assert source.status_code == 200, source.text
            edge = client.post(
                "/api/v1/graph/edges", headers=headers,
                json={"source": "shared-source-node", "target": "shared-path-entity",
                      "type": "mentions", "properties": {}},
            )
            assert edge.status_code == 200, edge.text

        alpha = client.post(
            "/api/v1/search/hybrid",
            headers=alpha_headers,
            json={"query": "confidential retrieval material /workspace/secret.md", "limit": 10, "rerank": True},
        )
        beta = client.post(
            "/api/v1/search/hybrid",
            headers=beta_headers,
            json={"query": "confidential retrieval material /workspace/secret.md", "limit": 10, "rerank": True},
        )

        assert alpha.status_code == 200, alpha.text
        assert beta.status_code == 200, beta.text
        alpha_body, beta_body = alpha.json(), beta.json()
        assert alpha_body["reranked"] is True
        assert beta_body["reranked"] is True
        for body, own_marker, foreign_marker in (
            (alpha_body, "alpha confidential", "beta confidential"),
            (beta_body, "beta confidential", "alpha confidential"),
        ):
            assert body["results"]
            assert all(foreign_marker not in hit["text"] for hit in body["results"])
            assert any(own_marker in hit["text"] for hit in body["results"])
            assert any("graph expansion only" in hit["text"] for hit in body["results"])
            # Diagnostics must describe only the authorized partition's candidates.
            assert body["fusion"]["channels"]["lexical"]["candidate_count"] == 1
            lexical_candidates = body["fusion"]["channels"]["lexical"]["candidates"]
            assert len(lexical_candidates) == 1
            assert lexical_candidates[0]["source_id"] == "same-source"
            assert lexical_candidates[0]["title"] == ("Alpha" if own_marker.startswith("alpha") else "Beta")
            vector_candidates = body["fusion"]["channels"]["vector"]["candidates"]
            assert body["fusion"]["channels"]["vector"]["candidate_count"] == len(vector_candidates) > 0
            assert all(candidate["title"] == ("Alpha" if own_marker.startswith("alpha") else "Beta")
                       for candidate in vector_candidates)
            assert body["channels"]["vector"]["status"] == "ok"
            assert body["fusion"]["channels"]["graph"]["candidate_count"] == 1
            assert any(item.get("graph_rank") is not None for item in body["fusion"]["results"])
            assert any("graph expansion only" in hit["text"] for hit in body["results"])
            assert len(body["fusion"]["results"]) == 2
        # Hybrid reranks once, after retrieval and Graph expansion.
        # Every invocation must remain within the authenticated partition.
        assert len(reranker.calls) == 2
        assert all(reranker.calls)
        alpha_calls = reranker.calls[:1]
        beta_calls = reranker.calls[1:]
        assert alpha_calls and beta_calls
        assert all("beta confidential" not in item and "beta graph expansion only" not in item
                   for call in alpha_calls for item in call)
        assert all("alpha confidential" not in item and "alpha graph expansion only" not in item
                   for call in beta_calls for item in call)
        assert any("alpha graph expansion only" in item for call in alpha_calls for item in call)
        assert any("beta graph expansion only" in item for call in beta_calls for item in call)


def test_hybrid_knowledge_retrieval_is_partition_isolated(tmp_path, monkeypatch):
    class Embeddings:
        available = True

        def status(self):
            return {"ok": True, "ready": True, "model": "partition-test", "dimensions": 3}

        async def embed(self, texts, mode="document"):
            return [[1.0, 0.0, 0.0] for _ in texts]

    alpha_token, beta_token = "alpha-knowledge-secret", "beta-knowledge-secret"
    token_map = {
        hashlib.sha256(alpha_token.encode()).hexdigest(): {
            "subject": "alpha-client", "partitions": ["alpha"],
        },
        hashlib.sha256(beta_token.encode()).hexdigest(): {
            "subject": "beta-client", "partitions": ["beta"],
        },
    }
    monkeypatch.setenv("ENSEMBLE_PARTITION_TOKENS", json.dumps(token_map))
    monkeypatch.setenv("ENSEMBLE_AUTH_REQUIRED", "1")
    base = f"sqlite+aiosqlite:///{tmp_path / 'knowledge-partitions.db'}"
    asyncio.run(asyncio.to_thread(_ensure_partition_schema, base, "default"))
    app = create_app(
        database_url=base, embedding_provider=Embeddings(),
        run_reclamation_worker=False,
    )
    alpha_headers = {
        "Authorization": f"Bearer {alpha_token}",
        "X-Ensemble-Partition": "alpha",
    }
    beta_headers = {
        "Authorization": f"Bearer {beta_token}",
        "X-Ensemble-Partition": "beta",
    }

    with TestClient(app) as client:
        for headers, title, content, revision_id in (
            (alpha_headers, "Alpha knowledge", "alpha confidential knowledge material", "alpha-revision"),
            (beta_headers, "Beta knowledge", "beta confidential knowledge material", "beta-revision"),
        ):
            stored = client.post(
                "/api/v1/knowledge/records", headers=headers,
                json={
                    "source_id": "shared-knowledge-source",
                    "revision_sequence": 1, "revision_id": revision_id,
                    "title": title, "content": content,
                    "provenance": {"origin": "partition-test", "locator": "test://shared-source"},
                    "profiles": {"chunking": "window-v1@1", "tokenization": "lexical-v1@1"},
                },
            )
            assert stored.status_code == 200, stored.text
            assert stored.json()["stored"] is True

        alpha = client.post(
            "/api/v1/search/hybrid/knowledge", headers=alpha_headers,
            json={"query": "confidential knowledge material", "limit": 10},
        )
        beta = client.post(
            "/api/v1/search/hybrid/knowledge", headers=beta_headers,
            json={"query": "confidential knowledge material", "limit": 10},
        )

        assert alpha.status_code == 200, alpha.text
        assert beta.status_code == 200, beta.text
        alpha_results = alpha.json()["results"]
        beta_results = beta.json()["results"]
        assert alpha_results
        assert beta_results
        assert {item["metadata"]["source_id"] for item in alpha_results} == {"shared-knowledge-source"}
        assert {item["metadata"]["source_id"] for item in beta_results} == {"shared-knowledge-source"}
        assert all("alpha confidential" in item["document"] for item in alpha_results)
        assert all("beta confidential" not in item["document"] for item in alpha_results)
        assert all("beta confidential" in item["document"] for item in beta_results)
        assert all("alpha confidential" not in item["document"] for item in beta_results)


def test_memory_rest_partition_and_scope_reconciliation_are_isolated(tmp_path, monkeypatch):
    alpha_token, beta_token = "alpha-memory-secret", "beta-memory-secret"
    token_map = {
        hashlib.sha256(alpha_token.encode()).hexdigest(): {
            "subject": "alpha-client", "partitions": ["alpha"],
        },
        hashlib.sha256(beta_token.encode()).hexdigest(): {
            "subject": "beta-client", "partitions": ["beta"],
        },
    }
    monkeypatch.setenv("ENSEMBLE_PARTITION_TOKENS", json.dumps(token_map))
    monkeypatch.setenv("ENSEMBLE_AUTH_REQUIRED", "1")
    base = f"sqlite+aiosqlite:///{tmp_path / 'memory-partitions.db'}"
    asyncio.run(asyncio.to_thread(_ensure_partition_schema, base, "default"))
    app = create_app(database_url=base, run_reclamation_worker=False)
    alpha_headers = {
        "Authorization": f"Bearer {alpha_token}",
        "X-Ensemble-Partition": "alpha",
    }
    beta_headers = {
        "Authorization": f"Bearer {beta_token}",
        "X-Ensemble-Partition": "beta",
    }

    def import_source(client, headers, path, scope, content):
        import hashlib
        response = client.post(
            "/api/v1/memory/sources/import", headers=headers,
            json={
                "path": path, "content": content,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "metadata": {"scope": scope, "title": path.rsplit("/", 1)[-1]},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ingested"

    with TestClient(app) as client:
        import_source(
            client, alpha_headers, "/workspace/skills/search.md", "/workspace",
            "alpha confidential memory source",
        )
        import_source(
            client, beta_headers, "/workspace/skills/search.md", "/workspace",
            "beta confidential memory source",
        )
        import_source(
            client, beta_headers, "/workspace/other/file.md", "/workspace/other",
            "beta nested scope memory source",
        )

        for headers, event_text in (
            (alpha_headers, "alpha event payload"),
            (beta_headers, "beta event payload"),
        ):
            stored = client.put(
                "/api/v1/memory/memories/session/entries/shared-event",
                headers=headers, json={"entry": {"result": event_text}},
            )
            assert stored.status_code == 200, stored.text

        alpha_reconcile = client.post(
            "/api/v1/memory/sources/reconcile", headers=alpha_headers,
            json={"source": "claude-code", "scope": "/workspace", "paths": []},
        )
        assert alpha_reconcile.status_code == 200
        assert alpha_reconcile.json()["count"] == 1
        assert client.get(
            "/api/v1/memory/sources/status", headers=alpha_headers,
        ).json()["indexed_document_count"] == 0
        assert client.get(
            "/api/v1/memory/sources/status", headers=beta_headers,
        ).json()["indexed_document_count"] == 2

        beta_reconcile = client.post(
            "/api/v1/memory/sources/reconcile", headers=beta_headers,
            json={"source": "claude-code", "scope": "/workspace", "paths": []},
        )
        assert beta_reconcile.status_code == 200
        assert beta_reconcile.json()["count"] == 1
        assert client.get(
            "/api/v1/memory/sources/status", headers=beta_headers,
        ).json()["indexed_document_count"] == 1

        alpha_events = client.get(
            "/api/v1/memory/memories/session/entries", headers=alpha_headers,
        ).json()["entries"]
        beta_events = client.get(
            "/api/v1/memory/memories/session/entries", headers=beta_headers,
        ).json()["entries"]
        assert [item["result"] for item in alpha_events] == ["alpha event payload"]
        assert [item["result"] for item in beta_events] == ["beta event payload"]

        alpha_search = client.post(
            "/api/v1/memory/search/semantic", headers=alpha_headers,
            json={"query": "confidential memory source", "source": "claude-code"},
        ).json()["results"]
        beta_search = client.post(
            "/api/v1/memory/search/semantic", headers=beta_headers,
            json={"query": "nested scope memory source", "source": "claude-code"},
        ).json()["results"]
        assert alpha_search == []
        assert beta_search
        assert all(item["source"] == "claude-code" for item in beta_search)

