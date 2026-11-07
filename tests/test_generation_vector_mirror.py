import asyncio

import pytest
from alembic import command
from alembic.config import Config

from ensemble.chunking import Chunker
from ensemble.index_store import SqliteIndexStore
from ensemble.models import DocumentIn
from ensemble.services import DocumentService
from ensemble.store import SqlDocumentStore
from ensemble.chroma_vector_store import ChromaVectorStore


class _TestEmbeddings:
    available = True

    def status(self):
        return {"model": "test-embedding-model", "dimensions": 2}

    async def embed(self, texts, mode="document"):
        return [[1.0, 0.0] for _ in texts]


def test_vector_generation_mirror_failure_keeps_index_change_unacknowledged(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'vector-generation-mirror.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    async def exercise():
        documents = SqlDocumentStore(database_url)
        vectors = ChromaVectorStore(database_url=database_url)
        index = SqliteIndexStore(database_url)
        await documents.initialize()
        await vectors.initialize()
        await index.initialize()
        try:
            await index.create_generation("vector-mirror-active")
            required = (
                "index", "documents", "vectors", "memory", "knowledge",
                "index_reconciliation", "documents_reconciliation",
                "vectors_reconciliation", "memory_reconciliation",
                "knowledge_reconciliation",
            )
            for capability in required:
                await index.record_generation_checkpoint(
                    "vector-mirror-active", capability, {"cursor": 0}, sealed=True,
                )
            await index.acquire_generation_write_fence("vector-mirror-active", 0)
            status = await index.generation_status()
            await index.activate_generation(
                "vector-mirror-active", status["active_generation_id"], status["epoch"],
                {
                    "acknowledged_writes_verified": True,
                    "tombstones_verified": True,
                    "reconciled_through_change_id": 0,
                },
            )

            await index.create_generation("vector-mirror-observer")
            service = DocumentService(
                documents, Chunker(), _TestEmbeddings(), vectors, index,
            )
            profiles = {"chunking": "window-v1", "media_type": "text/plain"}
            decision = await index.record_revision(
                "vector-mirror-source", 1, "revision-1", profiles,
            )
            assert decision["decision"] == "accepted"

            original_mirror = vectors.upsert_generation

            async def fail_generation_mirror(*args, **kwargs):
                raise RuntimeError("simulated active-generation Vector write failure")

            vectors.upsert_generation = fail_generation_mirror
            request = DocumentIn(
                source_id="vector-mirror-source", revision=1,
                title="Vector mirror failure", content="persist base and mirror generation",
                chunk_profile="window-v1", media_type="text/plain",
            )
            with pytest.raises(RuntimeError, match="simulated active-generation Vector write failure"):
                await service.ingest(request, vector_collection="generation_test")

            changes = await index.list_generation_changes_after(
                "vector-mirror-observer", 0, 10,
            )
            assert len(changes) == 1
            assert changes[0]["acknowledged"] is False
            assert await vectors.query("generation_test", [1.0, 0.0], 10)

            vectors.upsert_generation = original_mirror
            retry_decision = await index.record_revision(
                "vector-mirror-source", 1, "revision-1", profiles,
            )
            assert retry_decision["decision"] == "duplicate"
            await service.ingest(request, vector_collection="generation_test")
            acknowledged = await index.acknowledge_generation_change(
                "vector-mirror-source", 1, False,
            )
            assert acknowledged == 1

            changes = await index.list_generation_changes_after(
                "vector-mirror-observer", 0, 10,
            )
            assert len(changes) == 1
            assert changes[0]["acknowledged"] is True
            generation_vectors = await vectors.query_generation(
                "vector-mirror-active", "generation_test", [1.0, 0.0], 10,
            )
            assert len(generation_vectors) == 1
            assert generation_vectors[0]["metadata"]["source_id"] == "vector-mirror-source"

            # A newer revision accepted without embeddings must remove stale
            # vectors from both the base and active-generation stores.
            second_profiles = {"chunking": "window-v1", "media_type": "text/plain"}
            second_decision = await index.record_revision(
                "vector-mirror-source", 2, "revision-2", second_profiles,
            )
            assert second_decision["decision"] == "accepted"
            second_request = DocumentIn(
                source_id="vector-mirror-source", revision=2,
                title="Vector mirror without embeddings",
                content="new revision intentionally has no embedding",
                chunk_profile="window-v1", media_type="text/plain",
            )
            result = await service.ingest(
                second_request, embed=False, vector_collection="generation_test",
            )
            assert result.vector_indexed is False
            await index.acknowledge_generation_change(
                "vector-mirror-source", 2, False,
            )
            assert await vectors.query("generation_test", [1.0, 0.0], 10) == []
            assert await vectors.query_generation(
                "vector-mirror-active", "generation_test", [1.0, 0.0], 10,
            ) == []
        finally:
            await index.close()
            await vectors.close()
            await documents.close()

    asyncio.run(exercise())
