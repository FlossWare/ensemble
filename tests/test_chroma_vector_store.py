import asyncio

from ensemble.chroma_vector_store import ChromaVectorStore
from ensemble.partitioning import RequestScope, reset_request_scope, set_request_scope


def test_chroma_vector_store_persists_searches_and_rejects_model_mismatch(tmp_path):
    async def exercise():
        store = ChromaVectorStore(tmp_path)
        await store.initialize()
        try:
            assert await store.upsert("test-vectors", "model-a", 2, [{
                "id": "record-1", "embedding": [1.0, 0.0],
                "document": "first document",
                "metadata": {"source_id": "source-1", "nested": {"revision": 1}},
            }]) == 1
            result = await store.query("test-vectors", [1.0, 0.0], 5)
            assert result[0]["id"] == "record-1"
            assert result[0]["score"] > 0.999
            assert result[0]["metadata"]["nested"] == {"revision": 1}
            assert await store.existing("test-vectors", ["record-1", "missing"]) == ["record-1"]
            assert (await store.list_collections())[0]["name"] == "test-vectors"
            try:
                await store.upsert("test-vectors", "model-b", 2, [{
                    "id": "record-2", "embedding": [0.0, 1.0],
                    "document": "wrong model", "metadata": {"source_id": "source-2"},
                }])
            except RuntimeError as exc:
                assert "model" in str(exc)
            else:
                raise AssertionError("model mismatch must fail closed")
        finally:
            await store.close()
    asyncio.run(exercise())


def test_chroma_vector_store_generation_and_source_lifecycle(tmp_path):
    async def exercise():
        store = ChromaVectorStore(tmp_path)
        await store.initialize()
        try:
            item = {
                "id": "record-1", "embedding": [1.0, 0.0],
                "document": "generation document",
                "metadata": {"source_id": "source-1", "url": "source-1"},
            }
            await store.upsert("test-vectors", "model-a", 2, [item])
            await store.upsert_generation("generation-1", "test-vectors", "model-a", 2, [item])
            assert len(await store.query_generation("generation-1", "test-vectors", [1.0, 0.0], 5)) == 1
            assert len(await store.list_records_for_source("test-vectors", "source-1")) == 1
            assert await store.tombstone_source("source-1") == 2
            assert await store.query("test-vectors", [1.0, 0.0], 5) == []
            assert await store.query_generation("generation-1", "test-vectors", [1.0, 0.0], 5) == []
            assert await store.restore_source("source-1") == 2
            assert len(await store.query("test-vectors", [1.0, 0.0], 5)) == 1
            assert await store.delete_generation("generation-1") == 1
            assert await store.delete_superseded("test-vectors", "source-1", "new-digest") == 1
            assert await store.query("test-vectors", [1.0, 0.0], 5) == []
        finally:
            await store.close()
    asyncio.run(exercise())


def test_chroma_vector_store_isolates_partitions(tmp_path):
    async def exercise():
        store = ChromaVectorStore(tmp_path)
        token = set_request_scope(RequestScope(partition_id="alpha"))
        try:
            await store.upsert("partition-vectors", "model-a", 2, [{
                "id": "same-id", "embedding": [1.0, 0.0],
                "document": "alpha only", "metadata": {"source_id": "alpha"},
            }])
        finally:
            reset_request_scope(token)
        token = set_request_scope(RequestScope(partition_id="beta"))
        try:
            assert await store.query("partition-vectors", [1.0, 0.0], 5) == []
            await store.upsert("partition-vectors", "model-a", 2, [{
                "id": "same-id", "embedding": [0.0, 1.0],
                "document": "beta only", "metadata": {"source_id": "beta"},
            }])
            results = await store.query("partition-vectors", [0.0, 1.0], 5)
            assert len(results) == 1
            assert results[0]["document"] == "beta only"
        finally:
            reset_request_scope(token)
    asyncio.run(exercise())
