import asyncio

import pytest
from sqlalchemy import select

from ensemble.ga_store import SqliteGAStore, ga_metadata, ga_promotions


def test_ga_runs_and_candidate_approvals_survive_store_restart(tmp_path):
    database = tmp_path / "ga.sqlite"
    url = f"sqlite+aiosqlite:///{database}"

    async def exercise():
        store = SqliteGAStore(url)
        async with store.engine.begin() as connection:
            await connection.run_sync(ga_metadata.create_all)
        run = await store.create_run({"seed": 42, "population_size": 15})
        result = {"best_by_system": {"compression": [{"rank": 1, "fitness": 0.5, "parameters": {"compression_level": 2.0}}]}}
        await store.update_run(run["id"], status="completed", result=result)
        promotion = await store.promote(
            run["id"], "compression", result["best_by_system"]["compression"][0],
            "reviewed candidate", "operator",
        )
        assert promotion["status"] == "approved_for_export"
        await store.close()

        reopened = SqliteGAStore(url)
        restored = await reopened.get_run(run["id"])
        assert restored["status"] == "completed"
        assert restored["result"] == result
        async with reopened.engine.connect() as connection:
            count = await connection.scalar(select(ga_promotions.c.id).limit(1))
            assert count is not None
        with pytest.raises(Exception):
            await reopened.promote(
                run["id"], "compression", result["best_by_system"]["compression"][0],
                "duplicate", "operator",
            )
        await reopened.close()

    asyncio.run(exercise())



def test_interrupted_ga_runs_are_failed_after_store_restart(tmp_path):
    database = tmp_path / "ga-recovery.sqlite"
    url = f"sqlite+aiosqlite:///{database}"

    async def exercise():
        store = SqliteGAStore(url)
        async with store.engine.begin() as connection:
            await connection.run_sync(ga_metadata.create_all)
        pending = await store.create_run({"seed": 1})
        running = await store.create_run({"seed": 2})
        completed = await store.create_run({"seed": 3})
        await store.update_run(running["id"], status="running")
        await store.update_run(completed["id"], status="completed", result={"ok": True})
        await store.close()

        reopened = SqliteGAStore(url)
        recovered = await reopened.recover_interrupted_runs()
        assert recovered == 2
        assert (await reopened.get_run(pending["id"]))["status"] == "failed"
        interrupted = await reopened.get_run(running["id"])
        assert interrupted["status"] == "failed"
        assert "process restart" in interrupted["error"]
        restored_completed = await reopened.get_run(completed["id"])
        assert restored_completed["status"] == "completed"
        assert restored_completed["result"] == {"ok": True}
        assert await reopened.recover_interrupted_runs() == 0
        await reopened.close()

    asyncio.run(exercise())
