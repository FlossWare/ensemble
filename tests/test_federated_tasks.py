from datetime import datetime, timezone
from uuid import uuid4

import pytest

from ensemble.federated_tasks import FederatedTaskRequest, FederatedTaskStore


def _request(**overrides):
    value = {
        "task_id": str(uuid4()),
        "idempotency_key": "request-1",
        "origin_instance_id": "origin-01",
        "target_capability": "search",
        "task_type": "search.query",
        "payload": {"query": "federated task"},
        "created_at": datetime(2026, 11, 7, tzinfo=timezone.utc).isoformat(),
    }
    value.update(overrides)
    return FederatedTaskRequest.model_validate(value)


def test_task_inbox_accepts_and_persists_request(tmp_path):
    store = FederatedTaskStore(tmp_path)
    store.initialize()
    request = _request()

    record, created = store.accept(request, "worker-02")

    assert created is True
    assert record.state == "accepted"
    assert record.origin_instance_id == "origin-01"
    assert record.target_capability == "search"

    restarted = FederatedTaskStore(tmp_path)
    assert restarted.get(request.task_id).model_dump() == record.model_dump()


def test_task_inbox_idempotent_retry_returns_existing_record(tmp_path):
    store = FederatedTaskStore(tmp_path)
    store.initialize()
    request = _request()

    first, created = store.accept(request, "worker-02")
    second, created_again = store.accept(request, "worker-02")

    assert created is True
    assert created_again is False
    assert second.task_id == first.task_id
    assert len(store.list()) == 1


def test_task_inbox_rejects_reused_idempotency_key_with_different_payload(tmp_path):
    store = FederatedTaskStore(tmp_path)
    store.initialize()
    store.accept(_request(), "worker-02")

    with pytest.raises(ValueError, match="reused"):
        store.accept(_request(task_id=str(uuid4()), payload={"query": "different"}), "worker-02")


def test_task_inbox_rejects_local_origin_and_oversized_payload(tmp_path):
    store = FederatedTaskStore(tmp_path)
    store.initialize()

    with pytest.raises(ValueError, match="remote instance"):
        store.accept(_request(origin_instance_id="worker-02"), "worker-02")

    with pytest.raises(ValueError, match="1 MiB"):
        _request(payload={"large": "x" * 1_100_000})


def test_task_inbox_claim_is_single_owner_and_completion_is_durable(tmp_path):
    store = FederatedTaskStore(tmp_path)
    store.initialize()
    request = _request()
    store.accept(request, "worker-02")

    claimed = store.claim_next("worker-a", ["search.query"])
    assert claimed.state == "running"
    assert claimed.worker_id == "worker-a"
    assert claimed.attempt == 1
    assert store.claim_next("worker-b", ["search.query"]) is None

    assert store.complete(request.task_id, "worker-a", {"count": 1, "hits": []})
    completed = store.get(request.task_id)
    assert completed.state == "succeeded"
    assert completed.result == {"count": 1, "hits": []}
    assert completed.worker_id is None


def test_task_inbox_recovers_interrupted_running_work(tmp_path):
    store = FederatedTaskStore(tmp_path)
    store.initialize()
    request = _request()
    store.accept(request, "worker-02")
    store.claim_next("worker-a", ["search.query"])

    assert store.recover_interrupted() == 1
    recovered = store.get(request.task_id)
    assert recovered.state == "accepted"
    assert recovered.worker_id is None
    assert store.claim_next("worker-b", ["search.query"]).state == "running"
