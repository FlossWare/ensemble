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
)


class FakeQueue:
    def __init__(self):
        self.jobs = []

    async def enqueue(self, source_id):
        job = {"source_id": source_id, "state": "pending"}
        self.jobs.append(job)
        return job


def test_deletion_context_validates_identity_and_timezone():
    with pytest.raises(ValueError):
        DeletionContext("")
    with pytest.raises(ValueError):
        DeletionContext("source-1", retain_until=datetime.now())

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


def test_retention_decorator_defers_until_retention_expires():
    async def exercise():
        queue = FakeQueue()
        now = datetime(2026, 11, 6, tzinfo=timezone.utc)
        strategy = RetentionDeletionDecorator(
            DeferredReclamationStrategy(queue),
            clock=lambda: now,
        )
        retained = await strategy.execute(DeletionContext(
            "source-1", retain_until=now + timedelta(days=1),
        ))
        assert retained.state == "deferred"
        assert retained.retryable is True
        assert queue.jobs == []

        eligible = await strategy.execute(DeletionContext(
            "source-2", retain_until=now - timedelta(seconds=1),
        ))
        assert eligible.state == "queued"
        assert [job["source_id"] for job in queue.jobs] == ["source-2"]

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
        })
        assert response.status_code == 200
        assert response.json()["reclamation"] == {
            "source_id": "strategy-source",
            "state": "pending",
        }
        assert len(strategy.contexts) == 1
        assert strategy.contexts[0].source_id == "strategy-source"
