from datetime import datetime, timedelta, timezone

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
        missing = http.get("/api/v1/memory/memories/notes")
        assert missing.status_code == 404

        written = http.put(
            "/api/v1/memory/memories/notes",
            json={"content": "Ensemble memory is durable."},
        )
        assert written.status_code == 200
        assert http.get("/api/v1/memory/memories/notes").json() == {
            "ok": True,
            "content": "Ensemble memory is durable.",
        }


def test_memory_append_once_is_idempotent_and_rejects_key_reuse():
    with client() as http:
        payload = {"name": "session", "event_id": "event-1", "entry": {"result": "success"}}
        first = http.put("/api/v1/memory/memories/session/entries/event-1", json={"entry": payload["entry"]})
        retry = http.put("/api/v1/memory/memories/session/entries/event-1", json={"entry": payload["entry"]})
        conflict = http.put(
            "/api/v1/memory/memories/session/entries/event-1",
            json={"entry": {"result": "failure"}},
        )

        assert first.status_code == 200
        assert first.json()["status"] == "stored"
        assert retry.status_code == 200
        assert retry.json()["status"] == "duplicate"
        assert conflict.status_code == 409
        entries = http.get("/api/v1/memory/memories/session/entries").json()["entries"]
        assert len(entries) == 1


def test_memory_context_retrieval_prioritizes_parent_and_search_is_bounded():
    with client() as http:
        http.post(
            "/api/v1/memory/memories/execution/entries",
            json={
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
            "/api/v1/memory/memories/execution/entries",
            json={
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
            "/api/v1/memory/recall",
            json={"name": "execution", "context": context},
        ).json()["results"]
        assert recalled[0]["relation"] == "parent"

        http.put("/api/v1/memory/memories/execution", json={"content": "parent memory result"})
        http.put("/api/v1/memory/memories/unrelated", json={"content": "different document"})
        search = http.post(
            "/api/v1/memory/search/keywords",
            json={"keywords": ["parent", "memory"]},
        )
        assert search.status_code == 200
        assert search.json()["results"][0]["file"] == "execution"


def test_context_assembly_combines_memory_and_retrieval_with_token_budget():
    with client() as http:
        http.put("/api/v1/memory/memories/notes", json={
            "content": "# Retrieval\nMemory retrieval preserves provenance and source attribution.\n"
        })
        http.post("/api/v1/ingest/documents", json={
            "source_id": "context-doc", "revision": 1, "title": "Context retrieval",
            "content": "Hybrid retrieval combines lexical and vector evidence with source provenance.",
        })
        response = http.post("/api/v1/context/assemble", json={
            "query": "retrieval provenance", "limit": 10, "max_tokens": 300,
        })
        assert response.status_code == 200
        body = response.json()
        assert body["estimated_tokens"] <= body["max_tokens"]
        assert body["token_estimator"].startswith("UTF-8 character count")
        assert {item["source"] for item in body["sources"]} >= {
            "memory-document", "ensemble-retrieval",
        }
        assert "retrieval" in body["context"].casefold()


def test_context_assembly_includes_execution_memory_recall():
    with client() as http:
        http.post("/api/v1/memory/memories/execution/entries", json={
            "entry": {
                "decision": "Use Graph-linked source expansion.",
                "execution_context": {
                    "request_id": "context-request",
                    "execution_id": "parent-execution",
                    "objective": "finish retrieval",
                    "lineage": ["parent-execution"],
                },
            },
        })
        response = http.post("/api/v1/context/assemble", json={
            "query": "finish retrieval",
            "memory_name": "execution",
            "execution_context": {
                "request_id": "context-request",
                "execution_id": "child-execution",
                "parent_execution_id": "parent-execution",
                "objective": "continue retrieval",
                "lineage": ["parent-execution", "child-execution"],
            },
            "max_tokens": 200,
        })
        assert response.status_code == 200
        recall = next(item for item in response.json()["sources"]
                      if item["source"] == "memory-recall")
        assert recall["relation"] == "parent"
        assert "Graph-linked source expansion" in response.json()["context"]


def test_context_assembly_extractively_compresses_to_budget():
    from ensemble.api import _assemble_context

    source = (
        "A long irrelevant sentence about unrelated operations. "
        "The retrieval pipeline preserves provenance and ranks evidence. "
        "Another unrelated sentence about a different subsystem."
    )
    result = _assemble_context([{
        "source": "test", "title": "retrieval", "content": source, "score": 1.0,
    }], "retrieval provenance", 20)
    assert result["estimated_tokens"] <= 20
    assert result["compressed_count"] == 1
    assert result["truncated_count"] == 1
    assert "preserves provenance" in result["context"]


def test_memory_chunking_and_name_validation():
    with client() as http:
        http.put("/api/v1/memory/memories/chunked", json={"content": "# Intro\nalpha\n# Second\nbeta\n"})
        chunks = http.post(
            "/api/v1/memory/chunking", json={"name": "chunked", "size": 2000}
        ).json()["chunks"]
        assert [item["header"] for item in chunks] == ["Intro", "Second"]
        assert [item["content"] for item in chunks] == ["# Intro\nalpha\n", "# Second\nbeta\n"]

        invalid = http.put(
            "/api/v1/memory/memories/bad%20name",
            json={"content": "no"},
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
        first = http.post("/api/v1/memory/sources/import", json=payload)
        second = http.post("/api/v1/memory/sources/import", json=payload)
        assert first.status_code == 200
        assert first.json()["status"] == "ingested"
        assert second.status_code == 200
        assert second.json()["status"] == "unchanged"
        assert http.get("/api/v1/memory/sources/status").json()["indexed_document_count"] == 1

        semantic = http.post(
            "/api/v1/memory/search/semantic",
            json={"query": "provenance retrieval context", "source": "claude-code"},
        )
        assert semantic.status_code == 200
        assert semantic.json()["results"][0]["source"] == "claude-code"
        hybrid = http.post(
            "/api/v1/memory/search/hybrid",
            json={"query": "provenance retrieval"},
        )
        assert hybrid.status_code == 200
        assert hybrid.json()["results"]
        fused = hybrid.json()["results"][0]
        assert fused["fusion"]["algorithm"] == "rrf"
        assert fused["fusion"]["k"] == 60
        assert fused["fusion"]["ranks"]
        assert fused["fusion"]["contributions"]
        assert fused["score"] > 0

        reconciled = http.post(
            "/api/v1/memory/sources/reconcile",
            json={"source": "claude-code", "scope": "/workspace", "paths": []},
        )
        assert reconciled.status_code == 200
        assert reconciled.json()["count"] == 1
        assert http.get("/api/v1/memory/sources/status").json()["indexed_document_count"] == 0
        stale_search = http.post(
            "/api/v1/memory/search/semantic",
            json={"query": "provenance", "source": "claude-code"},
        )
        assert stale_search.json()["results"] == []


def test_memory_knowledge_ingest_rejects_bad_digest_and_scope_escape():
    import hashlib

    content = "A memory source."
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with client() as http:
        bad_digest = http.post(
            "/api/v1/memory/sources/import",
            json={
                "path": "/workspace/a.md",
                "content": content,
                "sha256": "0" * 64,
                "metadata": {"scope": "/workspace"},
            },
        )
        escaped = http.post(
            "/api/v1/memory/sources/import",
            json={
                "path": "/outside/a.md",
                "content": content,
                "sha256": digest,
                "metadata": {"scope": "/workspace"},
            },
        )
        assert bad_digest.status_code == 400
        assert escaped.status_code == 400



def test_memory_knowledge_ingest_rejects_dotdot_path_traversal():
    import hashlib

    from ensemble.memory_store import _validate_ingest

    content = "path validation"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    try:
        _validate_ingest(
            "/workspace/../outside/secret.md",
            content,
            digest,
            {"scope": "/workspace"},
        )
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal must be rejected")


def test_memory_knowledge_ingest_rejects_symlink_escape(tmp_path):
    import hashlib

    from ensemble.memory_store import _validate_ingest

    scope = tmp_path / "scope"
    outside = tmp_path / "outside"
    scope.mkdir()
    outside.mkdir()
    target = outside / "secret.md"
    target.write_text("outside", encoding="utf-8")
    link = scope / "linked.md"
    link.symlink_to(target)
    content = "caller supplied content"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    try:
        _validate_ingest(str(link), content, digest, {"scope": str(scope)})
    except ValueError:
        pass
    else:
        raise AssertionError("symlink escape must be rejected")



def test_memory_expiry_excludes_events_from_recall_but_keeps_audit_entries():
    now = datetime.now(timezone.utc)
    expired_at = (now - timedelta(minutes=1)).isoformat()
    expires_later = (now + timedelta(hours=1)).isoformat()
    context = {
        "request_id": "request-expiry",
        "execution_id": "current-execution",
        "objective": "retrieve active execution memory",
        "lineage": [],
    }
    with client() as http:
        for execution_id, result, expires_at in (
            ("expired-execution", "expired result", expired_at),
            ("active-execution", "active result", expires_later),
        ):
            response = http.post("/api/v1/memory/memories/execution/entries", json={
                "entry": {
                    "result": result,
                    "expires_at": expires_at,
                    "execution_context": {
                        "request_id": "request-expiry",
                        "execution_id": execution_id,
                        "objective": "prior work",
                        "lineage": [],
                    },
                },
            })
            assert response.status_code == 200

        recall = http.post("/api/v1/memory/recall", json={
            "name": "execution", "context": context, "limit": 10,
        })
        assert recall.status_code == 200
        assert [item["record"]["result"] for item in recall.json()["results"]] == ["active result"]

        # The event ledger remains available for audit even after an entry expires.
        entries = http.get("/api/v1/memory/memories/execution/entries").json()["entries"]
        assert len(entries) == 2

        invalid = http.post("/api/v1/memory/memories/execution/entries", json={
            "entry": {"result": "invalid expiry", "expires_at": "2026-11-02T12:00:00"},
        })
        assert invalid.status_code == 400


def test_memory_delete_removes_content_and_event_ledger():
    with client() as http:
        assert http.put("/api/v1/memory/memories/notes", json={"content": "private notes"}).status_code == 200
        assert http.put(
            "/api/v1/memory/memories/notes/entries/event-1",
            json={"entry": {"result": "private event"}},
        ).status_code == 200

        deleted = http.delete("/api/v1/memory/memories/notes")
        assert deleted.status_code == 200
        assert deleted.json()["memory_deleted"] is True
        assert deleted.json()["entries_deleted"] == 1
        assert http.get("/api/v1/memory/memories/notes").status_code == 404
        assert "notes" not in http.get("/api/v1/memory/memories").json()["memories"]
        assert http.delete("/api/v1/memory/memories/notes").status_code == 404


def test_context_assembly_uses_an_exact_configured_token_counter():
    from ensemble.api import _assemble_context

    class WordCounter:
        tokenizer_id = "test-whitespace"
        exact = True
        description = "exact whitespace-token count for deterministic tests"

        def count(self, text):
            return len(text.split())

        def truncate(self, text, max_tokens):
            return " ".join(text.split()[:max_tokens])

    result = _assemble_context([{
        "source": "memory", "title": "retrieval",
        "content": (
            "An unrelated sentence discusses other systems. "
            "Retrieval preserves provenance and source attribution. "
            "A final unrelated sentence discusses storage."
        ),
    }], "retrieval provenance", 9, WordCounter())
    assert result["tokenizer_id"] == "test-whitespace"
    assert result["tokenizer_exact"] is True
    assert result["estimated_tokens"] <= result["max_tokens"]
    assert result["compressed_count"] == 1
    assert "Retrieval preserves provenance" in result["context"]


def test_context_tokenizer_status_is_reported_by_rest():
    with client() as http:
        response = http.get("/api/v1/context/tokenizer")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["exact"] is False
        assert body["id"] == "approx-char4"



def test_memory_semantic_search_matches_identifiers_exactly_not_prefixes():
    from ensemble.memory_store import _semantic_search

    documents = [
        ("exact", "The deployment run identifier is build-2026-11-04 and remains active."),
        ("prefix", "The deployment run identifier is build-2026-11-040."),
    ]
    results = _semantic_search(documents, set(), "build-2026-11-04", 10, None)
    assert [item["file"] for item in results] == ["exact"]


def test_memory_semantic_search_matches_identifier_inside_composite_event_id():
    from ensemble.memory_store import _semantic_search

    results = _semantic_search(
        [("events", "Stored event id: session:run-4821:completion")],
        set(), "run-4821", 10, None,
    )
    assert [item["file"] for item in results] == ["events"]


def test_memory_semantic_search_handles_unicode_prose():
    from ensemble.memory_store import _semantic_search

    results = _semantic_search(
        [("unicode", "Обсуждаем архитектура распределённой системы.")],
        set(), "архитектура", 10, None,
    )
    assert [item["file"] for item in results] == ["unicode"]


def test_memory_semantic_search_handles_accented_prose():
    from ensemble.memory_store import _semantic_search

    results = _semantic_search(
        [("notes", "The café index preserves résumé metadata.")],
        set(), "café", 10, None,
    )
    assert [item["file"] for item in results] == ["notes"]


def test_memory_semantic_search_strips_trailing_period_before_identifier_classification():
    from ensemble.memory_store import _semantic_search

    results = _semantic_search(
        [("versions", "The exact schema version is 1.2.3 and remains current.")],
        set(), "1.2.3.", 10, None,
    )
    assert [item["file"] for item in results] == ["versions"]


def test_memory_semantic_search_returns_no_results_for_unknown_identifier():
    from ensemble.memory_store import _semantic_search

    results = _semantic_search(
        [("known", "The only identifier here is request-100.")],
        set(), "request-101", 10, None,
    )
    assert results == []


def test_memory_vectorizer_preserves_structured_identifiers():
    from ensemble.memory_store import _vectorize_text

    vector = _vectorize_text("run-4821 session:alpha.v2")
    assert "run-4821" in vector
    assert "run" in vector
    assert "4821" in vector
    assert "session:alpha.v2" in vector
    assert "session" in vector
    assert "alpha" in vector



def test_memory_semantic_search_limit_takes_precedence_over_top_k_alias():
    with client() as http:
        for index in range(5):
            response = http.put(
                f"/api/v1/memory/memories/retrieval-{index}",
                json={"content": f"# Retrieval {index}\n\nretrieval contract detail {index}"},
            )
            assert response.status_code == 200

        limited = http.post(
            "/api/v1/memory/search/semantic",
            json={"query": "retrieval contract detail", "limit": 1, "top_k": 4},
        )
        top_k_only = http.post(
            "/api/v1/memory/search/semantic",
            json={"query": "retrieval contract detail", "top_k": 3},
        )
        limit_only = http.post(
            "/api/v1/memory/search/semantic",
            json={"query": "retrieval contract detail", "limit": 2},
        )

        assert limited.status_code == 200
        assert top_k_only.status_code == 200
        assert limit_only.status_code == 200
        assert len(limited.json()["results"]) == 1
        assert len(top_k_only.json()["results"]) == 3
        assert len(limit_only.json()["results"]) == 2


def test_memory_append_once_preserves_timestamp_and_exposes_ledger_metadata():
    timestamp = "2026-10-08T23:00:00Z"
    payload = {"entry": {"event": "SessionEnd", "timestamp": timestamp}}
    with client() as http:
        first = http.put("/api/v1/memory/memories/events/entries/event-1", json=payload)
        duplicate = http.put("/api/v1/memory/memories/events/entries/event-1", json=payload)
        changed_timestamp = http.put(
            "/api/v1/memory/memories/events/entries/event-1",
            json={"entry": {"event": "SessionEnd", "timestamp": "2026-10-08T23:01:00Z"}},
        )
        assert first.status_code == 200
        assert first.json()["status"] == "stored"
        assert duplicate.status_code == 200
        assert duplicate.json()["status"] == "duplicate"
        assert changed_timestamp.status_code == 409
        assert "different payload" in changed_timestamp.json()["detail"]

        record = http.get("/api/v1/memory/memories/events/entries").json()["entries"][0]
        assert record["timestamp"] == timestamp
        assert len(record["payload_sha256"]) == 64
        assert record["captured_at"].endswith("Z")

        reserved = http.put(
            "/api/v1/memory/memories/events/entries/event-2",
            json={"entry": {"event": "SessionEnd", "captured_at": "caller-value"}},
        )
        assert reserved.status_code == 400


def test_memory_semantic_search_returns_content_and_respects_source_filters():
    import hashlib

    knowledge = "# Durable knowledge\\nDurable provenance is preserved in imported knowledge documents."
    digest = hashlib.sha256(knowledge.encode("utf-8")).hexdigest()
    with client() as http:
        assert http.put(
            "/api/v1/memory/memories/notes",
            json={"content": "# Memory\\nMemory provenance remains available to semantic search."},
        ).status_code == 200
        imported = http.post("/api/v1/memory/sources/import", json={
            "path": "/workspace/knowledge.md", "content": knowledge,
            "sha256": digest, "metadata": {"scope": "/workspace"},
        })
        assert imported.status_code == 200

        memory = http.post("/api/v1/memory/search/semantic", json={
            "query": "provenance", "source": "memory",
        })
        durable = http.post("/api/v1/memory/search/semantic", json={
            "query": "provenance", "source": "claude-code",
        })
        assert memory.status_code == durable.status_code == 200
        assert memory.json()["results"]
        assert durable.json()["results"]
        assert all(not item["file"].startswith("claude-code-") for item in memory.json()["results"])
        assert all(item["file"].startswith("claude-code-") for item in durable.json()["results"])
        assert "Memory provenance" in memory.json()["results"][0]["content"]
        assert "Durable provenance" in durable.json()["results"][0]["content"]


def test_memory_semantic_search_ranks_exact_operational_artifact_phrase_first():
    with client() as http:
        for name, content in (
            ("exact-artifact", "# Learning\\nThe operational learning artifact records the GA decision."),
            ("related-artifact", "# Learning\\nOperational learning artifacts can record many GA decisions."),
        ):
            assert http.put(
                f"/api/v1/memory/memories/{name}", json={"content": content},
            ).status_code == 200
        response = http.post("/api/v1/memory/search/semantic", json={
            "query": "operational learning artifact", "limit": 10,
        })
        assert response.status_code == 200
        results = response.json()["results"]
        assert results[0]["file"] == "exact-artifact"
        assert results[0]["score"] == 1.0


def test_memory_entry_endpoint_exposes_bounded_offset_pagination():
    with client() as http:
        for value in ("first", "second", "third"):
            assert http.post(
                "/api/v1/memory/memories/history/entries",
                json={"entry": {"value": value}},
            ).status_code == 200

        first_page = http.get("/api/v1/memory/memories/history/entries?limit=2&offset=0")
        second_page = http.get("/api/v1/memory/memories/history/entries?limit=2&offset=2")
        invalid_offset = http.get("/api/v1/memory/memories/history/entries?limit=2&offset=-1")

        assert first_page.status_code == 200
        assert [item["value"] for item in first_page.json()["entries"]] == ["second", "third"]
        assert first_page.json()["next_offset"] == 2
        assert second_page.status_code == 200
        assert [item["value"] for item in second_page.json()["entries"]] == ["first"]
        assert second_page.json()["next_offset"] is None
        assert invalid_offset.status_code == 400
