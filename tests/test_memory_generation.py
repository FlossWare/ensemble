import asyncio

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
            await reopened_memory.write("after-restart", "writes resumed after recovery")
            assert await reopened_memory.read("after-restart") == "writes resumed after recovery"
        finally:
            await reopened_memory.close()
            await restarted.close()

    asyncio.run(exercise())
