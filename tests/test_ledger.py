import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import create_async_engine

from ensemble.ledger import SqliteLedgerStore, envelope_for, ledger_entries


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
        source_event = {
            "event_id": "source-1", "event_type": "test", "source": "poc",
            "timestamp": "2026-11-07T12:00:00Z", "correlation_id": None,
            "payload": {"value": 1},
        }
        source = envelope_for("poc-ledger", 8, source_event, "2026-11-07T12:00:01Z", "a" * 64)
        first = await store.import_batch("poc-ledger", [source])
        second = await store.import_batch("poc-ledger", [source])
        assert first["imported"] == ["source-1"]
        assert second["duplicates"] == ["source-1"]
        conflict_event = dict(source_event, payload={"value": 2})
        conflict = envelope_for("poc-ledger", 9, conflict_event, "2026-11-07T12:00:02Z", source["entry_sha256"])
        with pytest.raises(ValueError, match="conflict"):
            await store.import_batch("poc-ledger", [conflict])
        assert (await store.status())["head_sequence"] == 1
        imported = await store.get("source-1")
        assert imported["provenance"]["source_sequence"] == 8
        await engine.dispose()
    asyncio.run(run())

def test_ledger_detects_payload_hash_entry_hash_chain_and_sequence_corruption(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tamper.db'}")
        store = SqliteLedgerStore(engine)
        await store.initialize()
        await store.append(event("one"))
        await store.append(event("two"))
        async with engine.begin() as conn:
            await conn.execute(update(ledger_entries).where(ledger_entries.c.sequence == 1).values(payload={"tampered": True}))
        result = await store.verify()
        assert not result["ok"] and result["failure_category"] == "payload_hash_mismatch"
        await engine.dispose()

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'entry-hash.db'}")
        store = SqliteLedgerStore(engine)
        await store.initialize()
        await store.append(event("one"))
        async with engine.begin() as conn:
            await conn.execute(update(ledger_entries).where(ledger_entries.c.sequence == 1).values(entry_sha256="f" * 64))
        result = await store.verify()
        assert not result["ok"] and result["failure_category"] == "entry_hash_mismatch"
        await engine.dispose()

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chain.db'}")
        store = SqliteLedgerStore(engine)
        await store.initialize()
        await store.append(event("one"))
        await store.append(event("two"))
        async with engine.begin() as conn:
            await conn.execute(update(ledger_entries).where(ledger_entries.c.sequence == 2).values(previous_entry_sha256="e" * 64))
        result = await store.verify()
        assert not result["ok"] and result["failure_category"] == "broken_hash_chain"
        await engine.dispose()

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gap.db'}")
        store = SqliteLedgerStore(engine)
        await store.initialize()
        await store.append(event("one"))
        await store.append(event("two"))
        async with engine.begin() as conn:
            await conn.execute(delete(ledger_entries).where(ledger_entries.c.sequence == 1))
        result = await store.verify()
        assert not result["ok"] and result["failure_category"] == "sequence_gap"
        await engine.dispose()
    asyncio.run(run())


def test_ledger_empty_status_and_external_checkpoint(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'checkpoint.db'}")
        store = SqliteLedgerStore(engine)
        await store.initialize()
        assert (await store.verify())["assurance"] == "empty_ledger"
        first = (await store.append(event("one")))["entry"]
        await store.append(event("two"))
        assert (await store.verify(from_sequence=2))["failure_category"] == "missing_checkpoint"
        checkpoint = {"ledger_id": first["ledger_id"], "sequence": 1, "entry_sha256": first["entry_sha256"]}
        assert (await store.verify(from_sequence=2, checkpoint=checkpoint))["ok"]
        await engine.dispose()
    asyncio.run(run())

def test_shared_contract_fixture(tmp_path):
    import json
    from pathlib import Path
    async def run():
        fixture_path = Path(__file__).resolve().parents[1] / "parity" / "ledger-contract.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'contract.db'}")
        store = SqliteLedgerStore(engine)
        await store.initialize()
        for item in fixture["events"]:
            await store.append(item)
        page = await store.pull(limit=fixture["expectations"]["max_page_size"])
        assert [item["sequence"] for item in page["entries"]] == [1, 2]
        assert page["next_after_sequence"] == 2 and page["has_more"]
        filtered = await store.pull(event_type=fixture["expectations"]["filter_event_type"], limit=fixture["expectations"]["filter_scan_limit"])
        assert [item["event_id"] for item in filtered["entries"]] == fixture["expectations"]["filtered_event_ids"]
        replayed = await store.replay(limit=10)
        assert [item["event_id"] for item in replayed["entries"]] == fixture["expectations"]["event_ids"]
        assert (await store.verify())["ok"]
        await engine.dispose()
    asyncio.run(run())
