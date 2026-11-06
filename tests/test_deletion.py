import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from ensemble.deletion import (
    AuditingDeletionDecorator,
    DeletionContext,
    DeletionResult,
    DeferredReclamationStrategy,
    ImmediateReclamationStrategy,
    RetentionDeletionDecorator,
    RetryingDeletionDecorator,
    DeletionStrategy,
    DeletionStrategyDecorator,
    ReclamationJob,
    ReclamationQueue,
    TContext,
    TValue,
)


class FakeQueue:
    def __init__(self):
        self.jobs = []

    async def enqueue(self, source_id, **metadata):
        job = {"source_id": source_id, "state": "pending", **metadata}
        self.jobs.append(job)
        return job


def test_deletion_context_validates_identity_and_timezone():
    with pytest.raises(ValueError):
        DeletionContext("")
    with pytest.raises(ValueError):
        DeletionContext("source-1", retain_until=datetime.now())

    with pytest.raises(ValueError):
        DeletionContext("source-1", actor=" " * 257)
    with pytest.raises(ValueError):
        DeletionContext("source-1", reason="x" * 4001)
    context = DeletionContext("source-1", actor="agent:test", reason="requested")
    assert context.source_id == "source-1"
    assert context.actor == "agent:test"


def test_deferred_and_immediate_strategies_preserve_durable_queueing():
    async def exercise():
        queue = FakeQueue()
        context = DeletionContext("source-1")
        deferred = await DeferredReclamationStrategy(queue).execute(context)
        assert deferred.state == "queued"
        assert deferred.value == {"source_id": "source-1", "state": "pending"}

        drained = []
        async def drain():
            drained.append(True)
            return 1

        immediate = await ImmediateReclamationStrategy(queue, drain).execute(
            DeletionContext("source-2")
        )
        assert immediate.state == "dispatched"
        assert immediate.processed_count == 1
        assert drained == [True]
        assert [job["source_id"] for job in queue.jobs] == ["source-1", "source-2"]

    asyncio.run(exercise())


def test_retention_decorator_persists_future_deadline_for_durable_queue():
    async def exercise():
        queue = FakeQueue()
        now = datetime(2026, 11, 6, tzinfo=timezone.utc)
        strategy = RetentionDeletionDecorator(DeferredReclamationStrategy(queue))
        retained = await strategy.execute(DeletionContext(
            "source-1", retain_until=now + timedelta(days=1),
        ))
        assert retained.state == "queued"
        assert retained.retryable is True
        assert queue.jobs[0]["retain_until"] == now + timedelta(days=1)

        eligible = await strategy.execute(DeletionContext(
            "source-2", retain_until=now - timedelta(seconds=1),
        ))
        assert eligible.state == "queued"
        assert [job["source_id"] for job in queue.jobs] == ["source-1", "source-2"]

    asyncio.run(exercise())


def test_retrying_decorator_retries_transient_failures():
    class FlakyStrategy:
        def __init__(self):
            self.calls = 0

        async def execute(self, context):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("temporary")
            return DeletionResult(context, "complete", value="done")

    async def exercise():
        inner = FlakyStrategy()
        delays = []
        async def no_sleep(delay):
            delays.append(delay)

        strategy = RetryingDeletionDecorator(
            inner,
            attempts=3,
            delay_seconds=lambda attempt: attempt / 10,
            sleep=no_sleep,
        )
        result = await strategy.execute("source-1")
        assert result.state == "complete"
        assert result.value == "done"
        assert inner.calls == 3
        assert delays == [0.1, 0.2]

    asyncio.run(exercise())


def test_retrying_decorator_does_not_retry_non_retryable_errors():
    class FailingStrategy:
        async def execute(self, context):
            raise ValueError("invalid request")

    async def exercise():
        strategy = RetryingDeletionDecorator(
            FailingStrategy(),
            attempts=3,
            retry_if=lambda error: not isinstance(error, ValueError),
        )
        with pytest.raises(ValueError):
            await strategy.execute("source-1")

    asyncio.run(exercise())


def test_audit_decorator_records_successful_deletion_result():
    async def exercise():
        queue = FakeQueue()
        audited = []
        async def audit(context, result):
            audited.append((context.source_id, result.state))

        strategy = AuditingDeletionDecorator(
            DeferredReclamationStrategy(queue),
            audit,
        )
        result = await strategy.execute(DeletionContext("source-1"))
        assert result.state == "queued"
        assert audited == [("source-1", "queued")]

    asyncio.run(exercise())


def test_api_uses_injected_deletion_strategy_for_tombstones():
    from fastapi.testclient import TestClient

    from ensemble.api import create_app
    from ensemble.store import InMemoryDocumentStore

    class RecordingStrategy:
        def __init__(self):
            self.contexts = []

        async def execute(self, context):
            self.contexts.append(context)
            return DeletionResult(
                source_id=context.source_id,
                state="queued",
                value={"source_id": context.source_id, "state": "pending"},
                retryable=True,
            )

    strategy = RecordingStrategy()
    with TestClient(create_app(
        store=InMemoryDocumentStore(),
        deletion_strategy=strategy,
    )) as client:
        revision = client.post("/api/v1/index/revisions", json={
            "source_id": "strategy-source",
            "revision_sequence": 1,
            "revision_id": "revision-1",
            "profiles": {"chunk": "window-v1"},
        })
        assert revision.status_code == 200

        response = client.post("/api/v1/index/tombstones", json={
            "source_id": "strategy-source",
            "revision_sequence": 2,
            "actor": "human:scot",
            "reason": "test deletion",
            "retain_until": "2026-12-01T00:00:00Z",
        })
        assert response.status_code == 200
        assert response.json()["reclamation"] == {
            "source_id": "strategy-source",
            "state": "pending",
        }
        assert len(strategy.contexts) == 1
        assert strategy.contexts[0].source_id == "strategy-source"
        assert strategy.contexts[0].actor == "human:scot"
        assert strategy.contexts[0].reason == "test deletion"
        assert strategy.contexts[0].retain_until == datetime(2026, 12, 1, tzinfo=timezone.utc)



def test_api_rejects_invalid_retention_metadata_before_tombstoning():
    from fastapi.testclient import TestClient
    from ensemble.api import create_app
    from ensemble.store import InMemoryDocumentStore

    with TestClient(create_app(store=InMemoryDocumentStore())) as client:
        created = client.post("/api/v1/index/revisions", json={
            "source_id": "invalid-retention-source",
            "revision_sequence": 1,
            "revision_id": "revision-1",
            "profiles": {"chunk": "window-v1"},
        })
        assert created.status_code == 200

        rejected = client.post("/api/v1/index/tombstones", json={
            "source_id": "invalid-retention-source",
            "revision_sequence": 2,
            "retain_until": "2026-11-06T12:00:00",
        })
        assert rejected.status_code == 400
        source = client.get("/api/v1/index/sources/invalid-retention-source")
        assert source.status_code == 200
        assert source.json()["source"]["tombstoned"] is False



def test_generic_deletion_contracts_preserve_context_and_result_parameters():
    from typing import get_type_hints

    assert DeletionStrategy.__parameters__ == (TContext, TValue)
    assert DeletionStrategyDecorator.__parameters__ == (TContext, TValue)
    assert RetryingDeletionDecorator.__parameters__ == (TContext, TValue)
    assert DeletionResult.__parameters__ == (TValue,)
    assert get_type_hints(ReclamationQueue.enqueue)["return"] is ReclamationJob



def test_api_records_deletion_strategy_audit():
    from fastapi.testclient import TestClient
    from ensemble.api import create_app
    from ensemble.store import InMemoryDocumentStore

    with TestClient(create_app(store=InMemoryDocumentStore(), run_reclamation_worker=False)) as client:
        client.post("/api/v1/index/revisions", json={
            "source_id": "audited-api-source",
            "revision_sequence": 1,
            "revision_id": "revision-1",
            "profiles": {"chunk": "window-v1"},
        })
        tombstone = client.post("/api/v1/index/tombstones", json={
            "source_id": "audited-api-source",
            "revision_sequence": 2,
            "actor": "human:operator",
            "reason": "test audit trail",
        })
        assert tombstone.status_code == 200
        audit = client.get("/api/v1/index/sources/audited-api-source/deletion-audit")
        assert audit.status_code == 200
        strategy_events = [item for item in audit.json()["events"] if item["event"] == "strategy_result"]
        assert strategy_events
        assert strategy_events[0]["actor"] == "human:operator"
        assert strategy_events[0]["reason"] == "test audit trail"



def test_api_restores_soft_deleted_source_and_cancels_pending_reclamation():
    from fastapi.testclient import TestClient
    from ensemble.api import create_app
    from ensemble.index_memory import InMemoryIndexStore
    from ensemble.knowledge_store import InMemoryKnowledgeStore
    from ensemble.store import InMemoryDocumentStore
    from ensemble.vector_memory import InMemoryVectorStore

    app = create_app(
        store=InMemoryDocumentStore(),
        index_store=InMemoryIndexStore(),
        knowledge_store=InMemoryKnowledgeStore(),
        vector_store=InMemoryVectorStore(),
        run_reclamation_worker=False,
    )
    with TestClient(app) as client:
        created = client.post("/api/v1/index/revisions", json={
            "source_id": "restore-api-source",
            "revision_sequence": 1,
            "revision_id": "revision-1",
            "profiles": {"chunk": "window-v1"},
        })
        assert created.status_code == 200
        deleted = client.post("/api/v1/index/tombstones", json={
            "source_id": "restore-api-source",
            "revision_sequence": 2,
            "actor": "human:operator",
            "reason": "reversible deletion test",
        })
        assert deleted.status_code == 200
        assert app.state.reclamation_store._jobs["restore-api-source"]["state"] == "pending"

        restored = client.post("/api/v1/index/restorations", json={
            "source_id": "restore-api-source",
            "revision_sequence": 3,
        })
        assert restored.status_code == 200
        assert restored.json()["source"]["tombstoned"] is False
        assert restored.json()["reclamation"] == "cancelled"
        assert app.state.reclamation_store._jobs["restore-api-source"]["state"] == "cancelled"
        audit = client.get("/api/v1/index/sources/restore-api-source/deletion-audit")
        assert any(item["event"] == "restored" for item in audit.json()["events"])



def test_api_refuses_restore_after_physical_reclamation_has_been_claimed():
    from fastapi.testclient import TestClient
    from ensemble.api import create_app
    from ensemble.index_memory import InMemoryIndexStore
    from ensemble.knowledge_store import InMemoryKnowledgeStore
    from ensemble.store import InMemoryDocumentStore
    from ensemble.vector_memory import InMemoryVectorStore

    app = create_app(
        store=InMemoryDocumentStore(),
        index_store=InMemoryIndexStore(),
        knowledge_store=InMemoryKnowledgeStore(),
        vector_store=InMemoryVectorStore(),
        run_reclamation_worker=False,
    )
    with TestClient(app) as client:
        client.post("/api/v1/index/revisions", json={
            "source_id": "restore-running-source",
            "revision_sequence": 1,
            "revision_id": "revision-1",
            "profiles": {"chunk": "window-v1"},
        })
        client.post("/api/v1/index/tombstones", json={
            "source_id": "restore-running-source",
            "revision_sequence": 2,
        })
        app.state.reclamation_store._jobs["restore-running-source"]["state"] = "running"
        rejected = client.post("/api/v1/index/restorations", json={
            "source_id": "restore-running-source",
            "revision_sequence": 3,
        })
        assert rejected.status_code == 409
        assert client.get("/api/v1/index/sources/restore-running-source").json()["source"]["tombstoned"] is True
