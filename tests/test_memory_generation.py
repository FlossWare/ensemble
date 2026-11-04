import asyncio
import sqlite3

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from ensemble.index_store import SqliteIndexStore
from ensemble.memory_store import (
    SqliteMemoryStore, memory_generation_records,
)
from ensemble.rebuild import IndexGenerationBuilder


def test_memory_generation_snapshot_and_reconciliation_are_durable(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'memory-generation.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    async def exercise():
        index = SqliteIndexStore(database_url)
        memory = SqliteMemoryStore(database_url)
        await index.initialize()
        await memory.initialize()
        try:
            await index.create_generation("memory-generation")
            await memory.write("alpha", "initial")
            builder = IndexGenerationBuilder(index, memory_store=memory)

            copied = await builder.copy_memory_page("memory-generation", page_size=10)
            assert copied["status"] == "complete"
            assert copied["record_count"] == 1

            await memory.write("alpha", "updated")
            await memory.append("alpha", {"event": "captured"}, event_id="event-1")
            reconciled = await builder.reconcile_memory_changes_page(
                "memory-generation", page_size=10,
            )
            assert reconciled["status"] == "caught_up"
            assert reconciled["page_count"] == 3

            async with memory.engine.connect() as connection:
                snapshot = (await connection.execute(
                    select(memory_generation_records.c.snapshot).where(
                        memory_generation_records.c.generation_id == "memory-generation",
                        memory_generation_records.c.name == "alpha",
                    )
                )).scalar_one()
            assert snapshot["content"] == "updated"
            assert snapshot["entries"] == [{"event": "captured", "event_id": "event-1", "timestamp": snapshot["entries"][0]["timestamp"]}]
            assert await memory.pending_memory_generation_changes("memory-generation") == 0
        finally:
            await memory.close()
            await index.close()

    asyncio.run(exercise())


def test_memory_writes_are_blocked_while_generation_write_fence_is_held(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'memory-fence.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    async def exercise():
        index = SqliteIndexStore(database_url)
        memory = SqliteMemoryStore(database_url)
        await index.initialize()
        await memory.initialize()
        try:
            await index.create_generation("memory-fence")
            await index.acquire_generation_write_fence("memory-fence", through_change_id=0)
            try:
                try:
                    await memory.write("blocked", "must not mutate during cutover")
                    raise AssertionError("Memory write passed through the generation fence")
                except RuntimeError as exc:
                    assert "fenced" in str(exc)
            finally:
                await index.release_generation_write_fence("memory-fence")
            await memory.write("allowed", "fence released")
            assert await memory.read("allowed") == "fence released"
        finally:
            await memory.close()
            await index.close()

    asyncio.run(exercise())



def test_interrupted_generation_fence_is_released_after_restart(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'memory-fence-recovery.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    async def exercise():
        index = SqliteIndexStore(database_url)
        memory = SqliteMemoryStore(database_url)
        await index.initialize()
        await memory.initialize()
        await index.create_generation("interrupted-fence")
        for capability in (
            "index_reconciliation", "documents_reconciliation",
            "vectors_reconciliation", "memory_reconciliation",
            "knowledge_reconciliation",
        ):
            await index.record_generation_checkpoint(
                "interrupted-fence", capability, {"cursor": 0}, sealed=True,
            )
        await index.acquire_generation_write_fence("interrupted-fence", through_change_id=0)
        await index.close()

        restarted = SqliteIndexStore(database_url)
        reopened_memory = SqliteMemoryStore(database_url)
        await restarted.initialize()
        await reopened_memory.initialize()
        try:
            recovered = await restarted.recover_generation_write_fence()
            assert recovered["recovered"] is True
            assert recovered["generation_id"] == "interrupted-fence"
            status = await restarted.generation_status()
            generation = next(item for item in status["generations"] if item["generation_id"] == "interrupted-fence")
            assert all(
                generation["checkpoints"][name]["sealed"] is False
                for name in (
                    "index_reconciliation", "documents_reconciliation",
                    "vectors_reconciliation", "memory_reconciliation",
                    "knowledge_reconciliation",
                )
            )
            await reopened_memory.write("after-restart", "writes resumed after recovery")
            assert await reopened_memory.read("after-restart") == "writes resumed after recovery"
        finally:
            await reopened_memory.close()
            await restarted.close()

    asyncio.run(exercise())



def test_memory_mutations_continue_mirroring_into_the_active_generation(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'memory-active-generation.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    async def exercise():
        index = SqliteIndexStore(database_url)
        memory = SqliteMemoryStore(database_url)
        await index.initialize()
        await memory.initialize()
        try:
            await index.create_generation("memory-active-generation")
            await memory.write("shared-memory", "before activation")
            builder = IndexGenerationBuilder(index, memory_store=memory)
            await builder.copy_memory_page("memory-active-generation", page_size=10)
            await builder.reconcile_memory_changes_page("memory-active-generation", page_size=10)

            for capability in ("index", "documents", "vectors", "knowledge"):
                await index.record_generation_checkpoint(
                    "memory-active-generation", capability, {"empty": True}, sealed=True,
                )
            for capability in (
                "index_reconciliation", "documents_reconciliation",
                "vectors_reconciliation", "knowledge_reconciliation",
            ):
                await index.record_generation_checkpoint(
                    "memory-active-generation", capability, {"cursor": 0}, sealed=False,
                )

            await builder.fence_reconciled_generation("memory-active-generation")
            status = await index.generation_status()
            activated = await index.activate_generation(
                "memory-active-generation", status["active_generation_id"],
                status["epoch"], {
                    "acknowledged_writes_verified": True,
                    "tombstones_verified": True,
                    "reconciled_through_change_id": 0,
                },
            )
            assert activated["active_generation_id"] == "memory-active-generation"

            await memory.write("shared-memory", "after activation")
            snapshot = await memory.get_memory_generation_snapshot(
                "memory-active-generation", "shared-memory",
            )
            assert snapshot["content"] == "after activation"
        finally:
            await memory.close()
            await index.close()

    asyncio.run(exercise())



def test_memory_generation_migration_preserves_existing_memory_rows(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'memory-migration.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0027_generation_reclamation_merge")

    with sqlite3.connect(tmp_path / "memory-migration.db") as connection:
        connection.execute(
            "INSERT INTO ensemble_memories (name, content, updated_at) VALUES (?, ?, ?)",
            ("preexisting-memory", "preserve this content", "2026-11-04T00:00:00+00:00"),
        )

    command.upgrade(config, "head")

    async def exercise():
        memory = SqliteMemoryStore(database_url)
        await memory.initialize()
        try:
            assert await memory.read("preexisting-memory") == "preserve this content"
            assert await memory.get_memory_snapshot("preexisting-memory") == {
                "name": "preexisting-memory",
                "content": "preserve this content",
                "entries": [],
                "source": None,
                "updated_at": "2026-11-04T00:00:00",
            }
        finally:
            await memory.close()

    asyncio.run(exercise())
