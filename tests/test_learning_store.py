import asyncio

from ensemble.learning_store import SqliteLearningStore, learning_metadata
from ensemble.memory_store import SqliteMemoryStore, memory_metadata
from ensemble.thompson_store import SqliteThompsonStore, thompson_metadata


def test_learning_outcomes_artifacts_and_thompson_events_survive_restart(tmp_path):
    database = tmp_path / "learning.sqlite"
    url = f"sqlite+aiosqlite:///{database}"
    artifact = {
        "schema": "learning-artifact", "version": "0.1",
        "artifact_type": "ga-parameters", "source": "ensemble",
        "created_at": "2026-11-01T00:00:00+00:00",
        "provenance": {}, "payload": {"run_id": "ga-1", "parameters": {"x": 0.5}},
    }

    async def exercise():
        learning = SqliteLearningStore(url)
        memory = SqliteMemoryStore(url)
        thompson = SqliteThompsonStore(url)
        async with learning.engine.begin() as connection:
            await connection.run_sync(learning_metadata.create_all)
        async with memory.engine.begin() as connection:
            await connection.run_sync(memory_metadata.create_all)
        async with thompson.engine.begin() as connection:
            await connection.run_sync(thompson_metadata.create_all)
        await learning.initialize()
        await memory.initialize()
        await thompson.initialize()

        prepared = await learning.prepare_outcome(
            task_id="durable-1", task_type="testing", model="sonnet",
            rating=5, tokens=99, cost=0.012,
        )
        assert prepared["status"] == "created"
        await memory.append(
            "learning-outcomes", {"task_id": "durable-1", "rating": 5}, event_id="durable-1"
        )
        assert await thompson.record_outcome_once("durable-1", "sonnet", True, 0.012, 99) is True
        await learning.complete_outcome("durable-1", True, True)
        assert await learning.record_artifact(artifact) == "stored"
        await learning.close()
        await memory.close()
        await thompson.close()

        learning = SqliteLearningStore(url)
        memory = SqliteMemoryStore(url)
        thompson = SqliteThompsonStore(url)
        assert (await learning.prepare_outcome(
            task_id="durable-1", task_type="testing", model="sonnet",
            rating=5, tokens=99, cost=0.012,
        ))["status"] == "duplicate"
        assert await memory.append(
            "learning-outcomes", {"task_id": "durable-1", "rating": 5}, event_id="durable-1"
        ) == {"status": "duplicate", "event_id": "durable-1"}
        assert await thompson.record_outcome_once("durable-1", "sonnet", True, 0.012, 99) is False
        assert (await thompson.get_state())["models"]["sonnet"]["calls"] == 1
        assert await learning.record_artifact(artifact) == "duplicate"
        assert len(await learning.list_artifacts()) == 1
        await learning.close()
        await memory.close()
        await thompson.close()

    asyncio.run(exercise())
