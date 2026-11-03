"""Resumable generation-building operations.

The builder deliberately performs one bounded page per call. A caller can
schedule repeated calls, inspect the durable checkpoint, and resume after a
process restart without holding a long-lived transaction.
"""

from __future__ import annotations

from typing import Any


class IndexGenerationBuilder:
    def __init__(self, index_store: Any, document_store: Any | None = None, vector_store: Any | None = None):
        self.index_store = index_store
        self.document_store = document_store
        self.vector_store = vector_store

    async def copy_index_sources(
        self, generation_id: str, *, page_size: int = 500,
    ) -> dict[str, Any]:
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 10000:
            raise ValueError("page_size must be between 1 and 10000")

        status = await self.index_store.generation_status()
        generation = next(
            (item for item in status["generations"] if item["generation_id"] == generation_id),
            None,
        )
        if generation is None or generation["state"] != "building":
            raise RuntimeError("generation is not in building state")

        checkpoints = generation.get("checkpoints") or {}
        previous = checkpoints.get("index") or {}
        if previous.get("sealed") is True:
            value = previous.get("value") or {}
            return {
                "generation_id": generation_id,
                "status": "complete",
                "page_count": 0,
                "source_count": value.get("source_count", 0),
                "tombstone_count": value.get("tombstone_count", 0),
                "cursor": value.get("cursor"),
                "upper_bound": value.get("source_upper_bound"),
            }

        prior_value = previous.get("value") or {}
        if prior_value:
            upper_bound = prior_value.get("source_upper_bound")
            cursor = prior_value.get("cursor")
            source_count = int(prior_value.get("source_count", 0))
            tombstone_count = int(prior_value.get("tombstone_count", 0))
        else:
            upper_bound = await self.index_store.source_key_upper_bound()
            cursor = None
            source_count = 0
            tombstone_count = 0

        page = await self.index_store.list_sources_after(
            cursor, upper_bound, page_size,
        )
        for source in page:
            await self.index_store.put_generation_source(generation_id, source)

        next_cursor = page[-1]["source_id"] if page else cursor
        source_count += len(page)
        tombstone_count += sum(1 for source in page if source["tombstoned"])
        complete = (
            upper_bound is None
            or next_cursor == upper_bound
            or len(page) < page_size
        )
        checkpoint = {
            "source_upper_bound": upper_bound,
            "cursor": next_cursor,
            "source_count": source_count,
            "tombstone_count": tombstone_count,
        }
        await self.index_store.record_generation_checkpoint(
            generation_id, "index", checkpoint, sealed=complete,
        )
        return {
            "generation_id": generation_id,
            "status": "complete" if complete else "in_progress",
            "page_count": len(page),
            "source_count": source_count,
            "tombstone_count": tombstone_count,
            "cursor": next_cursor,
            "upper_bound": upper_bound,
        }


    async def copy_documents_page(
        self, generation_id: str, *, page_size: int = 500,
    ) -> dict[str, Any]:
        if self.document_store is None:
            raise RuntimeError("document store is required for the document generation pass")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 10000:
            raise ValueError("page_size must be between 1 and 10000")

        status = await self.index_store.generation_status()
        generation = next(
            (item for item in status["generations"] if item["generation_id"] == generation_id),
            None,
        )
        if generation is None or generation["state"] != "building":
            raise RuntimeError("generation is not in building state")
        checkpoints = generation.get("checkpoints") or {}
        index_checkpoint = checkpoints.get("index") or {}
        if index_checkpoint.get("sealed") is not True:
            raise RuntimeError("Index source copy must be sealed before document copy")
        previous = checkpoints.get("documents") or {}
        if previous.get("sealed") is True:
            value = previous.get("value") or {}
            return {
                "generation_id": generation_id, "status": "complete",
                "page_count": 0, "source_count": value.get("source_count", 0),
                "document_count": value.get("document_count", 0),
                "chunk_count": value.get("chunk_count", 0),
                "tombstone_count": value.get("tombstone_count", 0),
                "cursor": value.get("cursor"),
                "upper_bound": value.get("source_upper_bound"),
            }

        prior_value = previous.get("value") or {}
        index_value = index_checkpoint.get("value") or {}
        upper_bound = index_value.get("source_upper_bound")
        if prior_value:
            cursor = prior_value.get("cursor")
            source_count = int(prior_value.get("source_count", 0))
            document_count = int(prior_value.get("document_count", 0))
            chunk_count = int(prior_value.get("chunk_count", 0))
            tombstone_count = int(prior_value.get("tombstone_count", 0))
        else:
            cursor = None
            source_count = document_count = chunk_count = tombstone_count = 0

        page = await self.index_store.list_generation_sources_after(
            generation_id, cursor, upper_bound, page_size,
        )
        active_chunks = await self.document_store.all_chunks()
        by_source: dict[str, list[Any]] = {}
        for item in active_chunks:
            by_source.setdefault(item.document.source_id, []).append(item)

        for source in page:
            source_id = source["source_id"]
            if source["tombstoned"]:
                await self.document_store.tombstone_generation_source(
                    generation_id, source_id,
                )
                if self.vector_store is not None:
                    await self.vector_store.tombstone_generation_source(
                        generation_id, source_id,
                    )
                tombstone_count += 1
                continue

            grouped: dict[str, list[Any]] = {}
            for item in by_source.get(source_id, []):
                grouped.setdefault(str(item.document.id), []).append(item)
            for items in grouped.values():
                document = items[0].document
                chunks = [item.chunk for item in items]
                await self.document_store.upsert_generation(
                    generation_id, document, chunks,
                )
                document_count += 1
                chunk_count += len(chunks)

        next_cursor = page[-1]["source_id"] if page else cursor
        source_count += len(page)
        complete = (
            upper_bound is None
            or next_cursor == upper_bound
            or len(page) < page_size
        )
        checkpoint = {
            "source_upper_bound": upper_bound,
            "cursor": next_cursor,
            "source_count": source_count,
            "document_count": document_count,
            "chunk_count": chunk_count,
            "tombstone_count": tombstone_count,
        }
        await self.index_store.record_generation_checkpoint(
            generation_id, "documents", checkpoint, sealed=complete,
        )
        return {
            "generation_id": generation_id,
            "status": "complete" if complete else "in_progress",
            "page_count": len(page),
            "source_count": source_count,
            "document_count": document_count,
            "chunk_count": chunk_count,
            "tombstone_count": tombstone_count,
            "cursor": next_cursor,
            "upper_bound": upper_bound,
        }
