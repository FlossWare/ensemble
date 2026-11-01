from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.store import InMemoryDocumentStore


def client():
    return TestClient(create_app(store=InMemoryDocumentStore()))


def test_memory_health_and_readiness_share_ensemble_application():
    with client() as http:
        assert http.get("/api/v1/memory/health").json()["service"] == "memory"
        assert http.get("/api/v1/memory/ready").json()["ready"] is True
        assert "memory" in http.get("/ready").json()["capabilities"]


def test_memory_write_read_and_missing_record():
    with client() as http:
        missing = http.post("/api/v1/memory/read", json={"name": "notes"})
        assert missing.status_code == 404

        written = http.post(
            "/api/v1/memory/write",
            json={"name": "notes", "content": "Ensemble memory is durable."},
        )
        assert written.status_code == 200
        assert http.post("/api/v1/memory/read", json={"name": "notes"}).json() == {
            "ok": True,
            "content": "Ensemble memory is durable.",
        }


def test_memory_append_once_is_idempotent_and_rejects_key_reuse():
    with client() as http:
        payload = {"name": "session", "event_id": "event-1", "entry": {"result": "success"}}
        first = http.post("/api/v1/memory/append-once", json=payload)
        retry = http.post("/api/v1/memory/append-once", json=payload)
        conflict = http.post(
            "/api/v1/memory/append-once",
            json={"name": "session", "event_id": "event-1", "entry": {"result": "failure"}},
        )

        assert first.status_code == 200
        assert first.json()["status"] == "stored"
        assert retry.status_code == 200
        assert retry.json()["status"] == "duplicate"
        assert conflict.status_code == 409
        entries = http.post("/api/v1/memory/entries", json={"name": "session"}).json()["entries"]
        assert len(entries) == 1


def test_memory_context_retrieval_prioritizes_parent_and_search_is_bounded():
    with client() as http:
        http.post(
            "/api/v1/memory/append",
            json={
                "name": "execution",
                "entry": {
                    "result": "parent result",
                    "execution_context": {
                        "request_id": "request-1",
                        "execution_id": "parent-1",
                        "objective": "build memory",
                        "lineage": ["parent-1"],
                    },
                },
            },
        )
        http.post(
            "/api/v1/memory/append",
            json={
                "name": "execution",
                "entry": {
                    "result": "same request result",
                    "execution_context": {
                        "request_id": "request-1",
                        "execution_id": "sibling-1",
                        "objective": "build memory",
                        "lineage": ["root-1", "sibling-1"],
                    },
                },
            },
        )
        context = {
            "request_id": "request-1",
            "execution_id": "child-1",
            "parent_execution_id": "parent-1",
            "objective": "continue memory",
            "lineage": ["parent-1", "child-1"],
        }
        recalled = http.post(
            "/api/v1/memory/retrieve",
            json={"name": "execution", "context": context},
        ).json()["results"]
        assert recalled[0]["relation"] == "parent"

        http.post("/api/v1/memory/write", json={"name": "execution", "content": "parent memory result"})
        http.post("/api/v1/memory/write", json={"name": "unrelated", "content": "different document"})
        search = http.post(
            "/api/v1/memory/search",
            json={"keywords": ["parent", "memory"]},
        )
        assert search.status_code == 200
        assert search.json()["results"][0]["file"] == "execution"


def test_memory_chunking_and_name_validation():
    with client() as http:
        http.post("/api/v1/memory/write", json={"name": "chunked", "content": "abcdefghij"})
        chunks = http.post(
            "/api/v1/memory/chunk", json={"name": "chunked", "size": 4}
        ).json()["chunks"]
        assert [item["content"] for item in chunks] == ["abcd", "efgh", "ij"]

        invalid = http.post(
            "/api/v1/memory/write",
            json={"name": "../outside", "content": "no"},
        )
        assert invalid.status_code == 400



def test_memory_knowledge_ingest_is_idempotent_and_reconcile_marks_stale():
    import hashlib

    content = "# Search Engineering\nEnsemble memory preserves provenance and retrieval context."
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    payload = {
        "path": "/workspace/skills/search.md",
        "content": content,
        "sha256": digest,
        "metadata": {"scope": "/workspace", "title": "Search"},
    }
    with client() as http:
        first = http.post("/api/v1/memory/ingest", json=payload)
        second = http.post("/api/v1/memory/ingest", json=payload)
        assert first.status_code == 200
        assert first.json()["status"] == "ingested"
        assert second.status_code == 200
        assert second.json()["status"] == "unchanged"
        assert http.get("/api/v1/memory/knowledge/status").json()["indexed_document_count"] == 1

        semantic = http.post(
            "/api/v1/memory/search-semantic",
            json={"query": "provenance retrieval context", "source": "claude-code"},
        )
        assert semantic.status_code == 200
        assert semantic.json()["results"][0]["source"] == "claude-code"
        hybrid = http.post(
            "/api/v1/memory/search-hybrid",
            json={"query": "provenance retrieval"},
        )
        assert hybrid.status_code == 200
        assert hybrid.json()["results"]

        reconciled = http.post(
            "/api/v1/memory/reconcile",
            json={"source": "claude-code", "scope": "/workspace", "paths": []},
        )
        assert reconciled.status_code == 200
        assert reconciled.json()["count"] == 1
        assert http.get("/api/v1/memory/knowledge/status").json()["indexed_document_count"] == 0
        stale_search = http.post(
            "/api/v1/memory/search-semantic",
            json={"query": "provenance", "source": "claude-code"},
        )
        assert stale_search.json()["results"] == []


def test_memory_knowledge_ingest_rejects_bad_digest_and_scope_escape():
    import hashlib

    content = "A memory source."
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with client() as http:
        bad_digest = http.post(
            "/api/v1/memory/ingest",
            json={
                "path": "/workspace/a.md",
                "content": content,
                "sha256": "0" * 64,
                "metadata": {"scope": "/workspace"},
            },
        )
        escaped = http.post(
            "/api/v1/memory/ingest",
            json={
                "path": "/outside/a.md",
                "content": content,
                "sha256": digest,
                "metadata": {"scope": "/workspace"},
            },
        )
        assert bad_digest.status_code == 400
        assert escaped.status_code == 400
