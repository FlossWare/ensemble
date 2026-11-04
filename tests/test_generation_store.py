import asyncio

import pytest
from alembic import command
from alembic.config import Config

from ensemble.generation_store import SqliteGenerationStore


def _migrate(database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def test_generation_lifecycle_checkpoints_and_atomic_activation(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'generation.db'}"
    _migrate(database_url)

    async def exercise():
        store = SqliteGenerationStore(database_url)
        await store.initialize()
        await store.create("generation-a", snapshot_watermark=17)
        await store.checkpoint(
            "generation-a", "documents",
            {"cursor": "source-9", "copied": 9},
            sealed=True, watermark=17,
        )
        with pytest.raises(ValueError, match="sealed checkpoint is immutable"):
            await store.checkpoint(
                "generation-a", "documents",
                {"cursor": "source-10", "copied": 10},
                sealed=True, watermark=17,
            )

        await store.transition("generation-a", "reconciling")
        await store.acquire_fence("generation-a", 21)
        await store.transition("generation-a", "verifying", applied_watermark=21)
        await store.checkpoint("generation-a", "verification", {"verified": True}, sealed=True, watermark=21)
        await store.transition("generation-a", "ready")
        activated = await store.activate(
            "generation-a", expected_active_generation_id=None,
        )
        assert activated == {
            "active_generation_id": "generation-a",
            "previous_generation_id": None,
        }

        await store.create("generation-b", snapshot_watermark=21)
        await store.transition("generation-b", "reconciling")
        await store.acquire_fence("generation-b", 25)
        await store.transition("generation-b", "verifying", applied_watermark=25)
        await store.checkpoint("generation-b", "verification", {"verified": True}, sealed=True, watermark=25)
        await store.transition("generation-b", "ready")
        with pytest.raises(ValueError, match="active generation changed"):
            await store.activate(
                "generation-b", expected_active_generation_id=None,
            )
        activated = await store.activate(
            "generation-b", expected_active_generation_id="generation-a",
        )
        assert activated["active_generation_id"] == "generation-b"
        assert activated["previous_generation_id"] == "generation-a"

        status = await store.status()
        assert status["active_generation_id"] == "generation-b"
        states = {item["generation_id"]: item["state"] for item in status["generations"]}
        assert states == {"generation-a": "retired", "generation-b": "active"}
        await store.close()

    asyncio.run(exercise())


def test_generation_state_machine_rejects_skipped_phases(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'generation-invalid.db'}"
    _migrate(database_url)

    async def exercise():
        store = SqliteGenerationStore(database_url)
        await store.initialize()
        await store.create("candidate")
        with pytest.raises(ValueError, match="invalid generation transition"):
            await store.transition("candidate", "active")
        with pytest.raises(ValueError, match="unknown generation state"):
            await store.transition("candidate", "promoted")
        await store.close()

    asyncio.run(exercise())
