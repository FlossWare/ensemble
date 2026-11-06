import asyncio
import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from ensemble import api
from ensemble.api import create_app
from ensemble.crawl_store import InMemoryCrawlJobStore
from ensemble.store import InMemoryDocumentStore


def test_crawl_job_store_claim_complete_recovery_and_retry():
    store = InMemoryCrawlJobStore()
    job = asyncio.run(store.enqueue("https://example.com/", 5, 2))
    assert job["status"] == "pending"
    claimed = asyncio.run(store.claim_next())
    assert claimed["job_id"] == job["job_id"]
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1
    assert asyncio.run(store.claim_next()) is None

    # Recovery only reclaims work whose five-minute lease has expired.
    store._jobs[job["job_id"]]["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=301)
    ).isoformat()
    assert asyncio.run(store.recover_interrupted()) == 1
    interrupted = asyncio.run(store.get(job["job_id"]))
    assert interrupted["status"] == "interrupted"

    retried = asyncio.run(store.retry(job["job_id"]))
    assert retried["status"] == "pending"
    claimed = asyncio.run(store.claim_next())
    assert claimed["attempts"] == 2
    completed = asyncio.run(store.complete(job["job_id"], {"page_count": 2}))
    assert completed["status"] == "completed"
    assert completed["result"] == {"page_count": 2}


def test_crawl_job_store_failure_can_be_retried():
    store = InMemoryCrawlJobStore()
    job = asyncio.run(store.enqueue("https://example.com/", 5, 2))
    asyncio.run(store.claim_next())
    asyncio.run(store.fail(job["job_id"], "temporary failure"))
    failed = asyncio.run(store.get(job["job_id"]))
    assert failed["status"] == "failed"
    assert failed["last_error"] == "temporary failure"
    assert asyncio.run(store.retry(job["job_id"]))["status"] == "pending"


def test_crawl_job_api_persists_job_and_worker_completes_it(monkeypatch):
    async def fake_crawl(uri, max_pages=10, max_depth=1, trace=None):
        if callable(trace):
            await trace({"name": "fake_page_fetch", "status": "completed", "duration_ms": 0.1, "page": 1})
        return {
            "ok": True, "start_uri": uri, "page_count": 2,
            "pages": [{"final_uri": uri}, {"final_uri": uri + "docs"}],
            "attempted_pages": 2, "robots_policy": "applied",
        }

    monkeypatch.setattr(api, "_validate_public_uri", lambda uri: uri)
    monkeypatch.setattr(api, "crawl_uri", fake_crawl)
    app = create_app(store=InMemoryDocumentStore(), crawl_store=InMemoryCrawlJobStore())
    with TestClient(app) as client:
        created = client.post("/api/v1/ingest/urls/crawl-jobs", json={
            "uri": "https://example.com/", "max_pages": 5, "max_depth": 2,
        })
        assert created.status_code == 202
        job_id = created.json()["job"]["job_id"]
        assert created.json()["job"]["status"] == "pending"

        deadline = time.monotonic() + 3
        job = None
        while time.monotonic() < deadline:
            response = client.get(f"/api/v1/ingest/urls/crawl-jobs/{job_id}")
            job = response.json()["job"]
            if job["status"] == "completed":
                break
            time.sleep(0.02)
        assert job["status"] == "completed"
        assert job["attempts"] == 1
        assert job["result"]["page_count"] == 2
        trace_id = job["result"]["execution_trace_id"]
        trace_response = client.get(f"/api/v1/execution-traces/{trace_id}")
        assert trace_response.status_code == 200
        trace = trace_response.json()["trace"]
        assert trace["operation"] == "crawl_job"
        assert trace["status"] == "completed"
        assert [stage["name"] for stage in trace["stages"]] == [
            "fake_page_fetch", "crawl_job_execution",
        ]

        listed = client.get("/api/v1/ingest/urls/crawl-jobs")
        assert listed.status_code == 200
        assert listed.json()["jobs"][0]["job_id"] == job_id


def test_crawl_job_api_validates_bounds_before_persisting(monkeypatch):
    monkeypatch.setattr(api, "_validate_public_uri", lambda uri: uri)
    app = create_app(store=InMemoryDocumentStore(), crawl_store=InMemoryCrawlJobStore())
    with TestClient(app) as client:
        response = client.post("/api/v1/ingest/urls/crawl-jobs", json={
            "uri": "https://example.com/", "max_pages": 1000,
        })
        assert response.status_code == 400


def test_crawl_job_ingestion_is_checkpointed_per_source_and_retrievable(monkeypatch):
    async def fake_crawl(uri, max_pages=10, max_depth=1, trace=None):
        if callable(trace):
            await trace({"name": "fake_page_fetch", "status": "completed", "duration_ms": 0.1, "page": 1})
        return {
            "ok": True, "start_uri": uri, "page_count": 1,
            "pages": [{
                "final_uri": uri, "title": "Example guide",
                "text": "A useful guide about durable ingestion.",
                "media_type": "text/markdown",
            }],
            "attempted_pages": 1, "robots_policy": "applied",
        }

    monkeypatch.setattr(api, "_validate_public_uri", lambda uri: uri)
    monkeypatch.setattr(api, "crawl_uri", fake_crawl)
    app = create_app(store=InMemoryDocumentStore(), crawl_store=InMemoryCrawlJobStore())
    with TestClient(app) as client:
        created = client.post("/api/v1/ingest/urls/crawl-jobs", json={
            "uri": "https://example.com/guide", "max_pages": 1, "max_depth": 0,
        })
        assert created.status_code == 202
        job_id = created.json()["job"]["job_id"]

        deadline = time.monotonic() + 4
        job = None
        while time.monotonic() < deadline:
            job = client.get(f"/api/v1/ingest/urls/crawl-jobs/{job_id}").json()["job"]
            if job["status"] == "completed":
                break
            time.sleep(0.02)
        assert job["status"] == "completed"

        ingested = client.post(f"/api/v1/ingest/urls/crawl-jobs/{job_id}/ingest")
        assert ingested.status_code == 200
        payload = ingested.json()["ingestion"]
        assert payload["status"] == "completed"
        assert payload["pages"][0]["status"] == "completed"
        assert payload["pages"][0]["revision"] == 1

        replay = client.post(f"/api/v1/ingest/urls/crawl-jobs/{job_id}/ingest")
        assert replay.status_code == 200
        assert replay.json()["ingestion"]["pages"][0]["revision"] == 1
        assert replay.json()["ingestion"]["pages"][0]["status"] == "completed"


def test_crawl_job_readiness_is_part_of_service_readiness():
    app = create_app(store=InMemoryDocumentStore(), crawl_store=InMemoryCrawlJobStore())
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert "crawl_jobs" in response.json()["capabilities"]


def test_restart_recovery_marks_in_progress_page_ingestion_interrupted():
    store = InMemoryCrawlJobStore()
    job = asyncio.run(store.enqueue("https://example.com/", 1, 0, auto_ingest=True))
    asyncio.run(store.claim_next())
    asyncio.run(store.complete(job["job_id"], {
        "pages": [{"final_uri": "https://example.com/", "text": "page"}],
    }))
    asyncio.run(store.update_ingestion(job["job_id"], {
        "status": "running",
        "pages": [{
            "source_id": "https://example.com/", "revision": 1,
            "status": "running", "content_sha256": "a" * 64,
        }],
    }))

    store._jobs[job["job_id"]]["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=301)
    ).isoformat()
    recovered = asyncio.run(store.recover_interrupted())
    state = asyncio.run(store.get(job["job_id"]))["ingestion"]
    assert recovered == 1
    assert state["status"] == "partial"
    assert state["pages"][0]["status"] == "interrupted"
