import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from ensemble.ledger import SqliteLedgerStore


def event(event_id, payload=None, **extra):
    return {
        "event_id": event_id,
        "event_type": extra.pop("event_type", "session.prompt"),
        "source": extra.pop("source", "claude-code"),
        "timestamp": "2026-11-07T12:00:00Z",
        "correlation_id": extra.pop("correlation_id", "corr-1"),
        "payload": payload if payload is not None else {"text": event_id},
        **extra,
    }


def test_ledger_append_pull_verify_import_and_restart(tmp_path):
    async def run():
        db = tmp_path / "ledger.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
        store = SqliteLedgerStore(engine)
        await store.initialize()
        first = await store.append(event("e1"))
        second = await store.append(event("e2"))
        assert first["entry"]["sequence"] == 1
        assert second["entry"]["sequence"] == 2
        assert (await store.append(event("e1")))["status"] == "duplicate"
        with pytest.raises(ValueError, match="different content"):
            await store.append(event("e1", {"text": "changed"}))
        page = await store.pull(after_sequence=0, limit=1)
        assert [x["event_id"] for x in page["entries"]] == ["e1"]
        assert page["next_after_sequence"] == 1 and page["has_more"]
        page2 = await store.pull(after_sequence=page["next_after_sequence"], limit=10)
        assert [x["event_id"] for x in page2["entries"]] == ["e2"]
        assert (await store.pull(event_type="not-present", limit=1))["next_after_sequence"] == 1
        assert (await store.get("e2"))["sequence"] == 2
        assert (await store.verify())["ok"]
        assert (await store.verify(from_sequence=2))["failure_category"] == "missing_checkpoint"
        status = await store.status()
        await engine.dispose()

        engine2 = create_async_engine(f"sqlite+aiosqlite:///{db}")
        reopened = SqliteLedgerStore(engine2)
        await reopened.initialize()
        assert (await reopened.status()) == status
        assert (await reopened.pull())["head_sequence"] == 2
        await engine2.dispose()
    asyncio.run(run())


def test_ledger_concurrent_appends_allocate_unique_sequences(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}")
        store = SqliteLedgerStore(engine)
        await store.initialize()
        async def append(i):
            return await store.append(event(f"parallel-{i}"))
        results = await asyncio.gather(*(append(i) for i in range(20)), return_exceptions=True)
        errors = [item for item in results if isinstance(item, BaseException)]
        assert not errors, errors
        page = await store.pull(limit=100)
        assert [item["sequence"] for item in page["entries"]] == list(range(1, 21))
        await engine.dispose()
    asyncio.run(run())


def test_ledger_filter_cursor_and_finite_head(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'filters.db'}")
        store = SqliteLedgerStore(engine)
        await store.initialize()
        await store.append(event("a", event_type="alpha"))
        await store.append(event("b", event_type="beta"))
        await store.append(event("c", event_type="alpha"))
        head = (await store.status())["head_sequence"]
        page = await store.pull(after_sequence=0, through_sequence=head, limit=1, event_type="beta")
        assert page["entries"] == []
        assert page["next_after_sequence"] == 1 and page["has_more"]
        page = await store.pull(after_sequence=1, through_sequence=head, limit=1, event_type="beta")
        assert [x["event_id"] for x in page["entries"]] == ["b"]
        assert page["next_after_sequence"] == 2
        await engine.dispose()
    asyncio.run(run())


def test_ledger_import_is_idempotent_and_conflict_safe(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'import.db'}")
        store = SqliteLedgerStore(engine)
        await store.initialize()
        source = {
            "event_id": "source-1", "event_type": "test", "source": "poc",
            "timestamp": "2026-11-07T12:00:00Z", "correlation_id": None,
            "payload": {"value": 1}, "sequence": 8,
            "payload_sha256": __import__("hashlib").sha256(b'{"value":1}').hexdigest(),
            "entry_sha256": "source-hash", "provenance": {},
        }
        first = await store.import_batch("poc-ledger", [source])
        second = await store.import_batch("poc-ledger", [source])
        assert first["imported"] == ["source-1"]
        assert second["duplicates"] == ["source-1"]
        conflict = dict(source, payload={"value": 2})
        conflict["payload_sha256"] = __import__("hashlib").sha256(b'{"value":2}').hexdigest()
        with pytest.raises(ValueError, match="conflict"):
            await store.import_batch("poc-ledger", [conflict])
        assert (await store.status())["head_sequence"] == 1
        imported = await store.get("source-1")
        assert imported["provenance"]["source_sequence"] == 8
        await engine.dispose()
    asyncio.run(run())
