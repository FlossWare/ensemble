import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ensemble.execution_trace_store import InMemoryExecutionTraceStore


def test_in_memory_execution_trace_records_stage_order_and_terminal_status():
    store = InMemoryExecutionTraceStore()
    trace_id = "a" * 32
    asyncio.run(store.create({
        "trace_id": trace_id, "request_id": "request-1",
        "operation": "document_ingestion", "status": "running",
        "started_at": datetime.now(timezone.utc), "completed_at": None,
        "duration_ms": None, "attributes": {"source_key": "hashed"},
        "stages": [], "error_category": None,
    }))
    asyncio.run(store.record_stage(trace_id, {"name": "index_revision", "status": "completed", "duration_ms": 1.2}))
    asyncio.run(store.record_stage(trace_id, {"name": "document_materialization", "status": "completed", "duration_ms": 3.4}))
    asyncio.run(store.finish(trace_id, "completed", 5.0))
    result = asyncio.run(store.get(trace_id))
    assert result["status"] == "completed"
    assert [stage["name"] for stage in result["stages"]] == ["index_revision", "document_materialization"]
    assert result["attributes"] == {"source_key": "hashed"}


def test_execution_trace_api_lists_and_reads_persisted_ingestion_trace():
    from ensemble.api import create_app
    from ensemble.store import InMemoryDocumentStore

    app = create_app(store=InMemoryDocumentStore())
    with TestClient(app) as client:
        # Exercise the same store used by the app without requiring a real ingestion dependency.
        trace_store = app.state.execution_trace_store
        trace_id = "b" * 32
        import asyncio
        asyncio.run(trace_store.create({
            "trace_id": trace_id, "request_id": trace_id,
            "operation": "document_ingestion", "status": "running",
            "started_at": datetime.now(timezone.utc), "completed_at": None,
            "duration_ms": None, "attributes": {}, "stages": [], "error_category": None,
        }))
        listed = client.get("/api/v1/execution-traces")
        assert listed.status_code == 200
        assert listed.json()["traces"][0]["trace_id"] == trace_id
        detail = client.get(f"/api/v1/execution-traces/{trace_id}")
        assert detail.status_code == 200
        assert detail.json()["trace"]["status"] == "running"


def test_trace_terminal_state_is_immutable_and_failure_is_recorded():
    store = InMemoryExecutionTraceStore()
    trace_id = "d" * 32

    async def scenario():
        await store.create({
            "trace_id": trace_id, "request_id": trace_id,
            "operation": "retrieval", "status": "running",
            "started_at": datetime.now(timezone.utc), "completed_at": None,
            "duration_ms": None, "attributes": {}, "stages": [],
            "error_category": None,
        })
        await store.record_stage(trace_id, {
            "name": "lexical_vector_retrieval_and_fusion",
            "status": "failed", "duration_ms": 2.5,
            "error_category": "RuntimeError",
        })
        await store.finish(trace_id, "failed", 2.7, "RuntimeError")
        await store.finish(trace_id, "completed", 3.0)
        result = await store.get(trace_id)
        assert result["status"] == "failed"
        assert result["error_category"] == "RuntimeError"
        assert result["stages"][0]["status"] == "failed"

    asyncio.run(scenario())


def test_in_memory_trace_store_enforces_retention_limit(monkeypatch):
    import ensemble.execution_trace_store as trace_module

    monkeypatch.setattr(trace_module, "MAX_TRACE_RECORDS", 2)
    store = InMemoryExecutionTraceStore()

    async def scenario():
        for index in range(3):
            trace_id = f"{index:032x}"
            await store.create({
                "trace_id": trace_id, "request_id": trace_id,
                "operation": "retrieval", "status": "running",
                "started_at": datetime(2026, 1, 1, 0, 0, index, tzinfo=timezone.utc),
                "completed_at": None, "duration_ms": None,
                "attributes": {}, "stages": [], "error_category": None,
            })
        records = await store.list(10)
        assert len(records) == 2
        assert [record["trace_id"] for record in records] == [
            f"{2:032x}", f"{1:032x}",
        ]

    asyncio.run(scenario())
