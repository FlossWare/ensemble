import asyncio
import time

from fastapi.testclient import TestClient
from alembic import command
from alembic.config import Config
from datetime import datetime, timedelta, timezone
from sqlalchemy import update

from ensemble import api
from ensemble.api import create_app
from ensemble.crawl_store import InMemoryCrawlJobStore, SqliteCrawlJobStore
from ensemble.source_schedule_store import InMemorySourceScheduleStore, SqliteSourceScheduleStore, schedules as schedule_table
from ensemble.store import InMemoryDocumentStore


def test_due_source_schedule_dispatches_auto_ingest_crawl_job():
    crawl_store = InMemoryCrawlJobStore()
    schedules = InMemorySourceScheduleStore(crawl_store)
    schedule = asyncio.run(schedules.create(
        "https://example.com/docs", 3600, 5, 2, run_immediately=True,
    ))
    assert asyncio.run(schedules.dispatch_due()) == 1
    updated = asyncio.run(schedules.get(schedule["schedule_id"]))
    assert updated["last_job_id"]
    job = asyncio.run(crawl_store.get(updated["last_job_id"]))
    assert job["status"] == "pending"
    assert job["auto_ingest"] is True
    assert job["schedule_id"] == schedule["schedule_id"]
    assert asyncio.run(schedules.dispatch_due()) == 0


def test_source_schedule_can_be_paused_and_resumed():
    schedules = InMemorySourceScheduleStore(InMemoryCrawlJobStore())
    schedule = asyncio.run(schedules.create(
        "https://example.com/docs", 3600, 5, 1, run_immediately=True,
    ))
    paused = asyncio.run(schedules.set_enabled(schedule["schedule_id"], False))
    assert paused["enabled"] is False
    assert asyncio.run(schedules.dispatch_due()) == 0
    resumed = asyncio.run(schedules.set_enabled(schedule["schedule_id"], True))
    assert resumed["enabled"] is True
    assert asyncio.run(schedules.dispatch_due()) == 1


def test_scheduled_source_refresh_crawls_and_ingests_automatically(monkeypatch):
    async def fake_crawl(uri, max_pages=10, max_depth=1, trace=None):
        if callable(trace):
            await trace({
                "name": "scheduled_page_fetch", "status": "completed",
                "duration_ms": 0.1, "page": 1,
            })
        return {
            "ok": True, "start_uri": uri, "page_count": 1,
            "pages": [{
                "final_uri": uri, "title": "Scheduled guide",
                "text": "Scheduled source refresh preserves current content.",
                "media_type": "text/markdown",
            }],
            "attempted_pages": 1, "robots_policy": "applied",
        }

    monkeypatch.setattr(api, "_validate_public_uri", lambda uri: uri)
    monkeypatch.setattr(api, "crawl_uri", fake_crawl)
    crawl_store = InMemoryCrawlJobStore()
    schedule_store = InMemorySourceScheduleStore(crawl_store)
    app = create_app(
        store=InMemoryDocumentStore(), crawl_store=crawl_store,
        source_schedule_store=schedule_store,
    )
    with TestClient(app) as client:
        created = client.post("/api/v1/ingest/urls/schedules", json={
            "uri": "https://example.com/guide",
            "interval_seconds": 3600, "max_pages": 1, "max_depth": 0,
        })
        assert created.status_code == 201
        schedule_id = created.json()["schedule"]["schedule_id"]

        deadline = time.monotonic() + 8
        job = None
        while time.monotonic() < deadline:
            schedule = client.get("/api/v1/ingest/urls/schedules").json()["schedules"][0]
            if schedule["last_job_id"]:
                job = client.get(
                    f"/api/v1/ingest/urls/crawl-jobs/{schedule['last_job_id']}"
                ).json()["job"]
                if job["status"] == "completed" and job.get("ingestion", {}).get("status") == "completed":
                    break
            time.sleep(0.05)

        assert schedule["schedule_id"] == schedule_id
        assert job is not None
        assert job["auto_ingest"] is True
        assert job["ingestion"]["status"] == "completed"
        assert job["ingestion"]["pages"][0]["status"] == "completed"
        trace_id = job["result"]["execution_trace_id"]
        trace_response = client.get(f"/api/v1/execution-traces/{trace_id}")
        assert trace_response.status_code == 200
        trace = trace_response.json()["trace"]
        assert trace["operation"] == "crawl_job"
        assert trace["status"] == "completed"
        assert [stage["name"] for stage in trace["stages"]] == [
            "scheduled_page_fetch", "crawl_job_execution", "automatic_ingestion",
        ]


def test_source_schedule_api_rejects_too_frequent_refresh(monkeypatch):
    monkeypatch.setattr(api, "_validate_public_uri", lambda uri: uri)
    app = create_app(store=InMemoryDocumentStore())
    with TestClient(app) as client:
        response = client.post("/api/v1/ingest/urls/schedules", json={
            "uri": "https://example.com/", "interval_seconds": 30,
        })
        assert response.status_code == 400

def test_source_schedule_does_not_overlap_an_active_scheduled_crawl():
    async def scenario():
        crawl_store = InMemoryCrawlJobStore()
        schedules = InMemorySourceScheduleStore(crawl_store)
        schedule = await schedules.create(
            "https://example.com/docs", 3600, 5, 2, run_immediately=True,
        )
        assert await schedules.dispatch_due() == 1
        first_job_id = (await schedules.get(schedule["schedule_id"]))["last_job_id"]
        # Force the interval due while the first job is pending and then running.
        schedules._items[schedule["schedule_id"]]["next_run_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        # Pending work must also suppress a duplicate dispatch.
        assert await schedules.dispatch_due() == 0
        claimed = await crawl_store.claim_next()
        assert claimed["job_id"] == first_job_id

        # The interval is still due, but the previous scheduled crawl is
        # running. Do not enqueue another copy of the same source refresh.
        assert await schedules.dispatch_due() == 0
        assert (await schedules.get(schedule["schedule_id"]))["last_job_id"] == first_job_id

        await crawl_store.complete(first_job_id, {"ok": True, "page_count": 1})
        assert await schedules.dispatch_due() == 1
        second_job_id = (await schedules.get(schedule["schedule_id"]))["last_job_id"]
        assert second_job_id != first_job_id
        second_job = await crawl_store.get(second_job_id)
        assert second_job["schedule_id"] == schedule["schedule_id"]

    asyncio.run(scenario())


def test_sqlite_source_schedule_dispatch_is_durable_and_prevents_overlap(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'scheduled-refresh.db'}"
    migration_config = Config("alembic.ini")
    migration_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migration_config, "head")

    async def scenario():
        schedules = SqliteSourceScheduleStore(database_url)
        jobs = SqliteCrawlJobStore(database_url)
        await schedules.initialize()
        await jobs.initialize()
        schedule = await schedules.create(
            "https://example.com/reference", 3600, 8, 2, run_immediately=True,
        )
        assert await schedules.dispatch_due() == 1
        first_job_id = (await schedules.get(schedule["schedule_id"]))["last_job_id"]

        # Force the interval due while the first job is pending and then
        # running. Neither state may create an overlapping refresh.
        async with schedules.engine.begin() as connection:
            await connection.execute(update(schedule_table).where(
                schedule_table.c.schedule_id == schedule["schedule_id"],
            ).values(next_run_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        assert await schedules.dispatch_due() == 0
        assert (await schedules.get(schedule["schedule_id"]))["last_job_id"] == first_job_id

        claimed = await jobs.claim_next()
        assert claimed["job_id"] == first_job_id
        assert await schedules.dispatch_due() == 0
        await jobs.complete(first_job_id, {"ok": True, "page_count": 2})
        assert await schedules.dispatch_due() == 1
        second_job_id = (await schedules.get(schedule["schedule_id"]))["last_job_id"]
        assert second_job_id != first_job_id

        await schedules.close()
        await jobs.close()

        # A new store instance sees both the schedule and its dispatched jobs.
        schedules = SqliteSourceScheduleStore(database_url)
        jobs = SqliteCrawlJobStore(database_url)
        await schedules.initialize()
        await jobs.initialize()
        persisted = await schedules.get(schedule["schedule_id"])
        assert persisted is not None
        assert persisted["last_job_id"] == second_job_id
        first = await jobs.get(first_job_id)
        second = await jobs.get(second_job_id)
        assert first["status"] == "completed"
        assert second["status"] == "pending"
        assert second["auto_ingest"] is True
        assert second["schedule_id"] == schedule["schedule_id"]
        await schedules.close()
        await jobs.close()

    asyncio.run(scenario())
