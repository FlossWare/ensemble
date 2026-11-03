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


    async def copy_vectors_page(
        self, generation_id: str, *, page_size: int = 500,
    ) -> dict[str, Any]:
        if self.vector_store is None:
            raise RuntimeError("vector store is required for the vector generation pass")
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
        if (checkpoints.get("index") or {}).get("sealed") is not True:
            raise RuntimeError("Index source copy must be sealed before vector copy")
        if (checkpoints.get("documents") or {}).get("sealed") is not True:
            raise RuntimeError("Document copy must be sealed before vector copy")

        previous = checkpoints.get("vectors") or {}
        if previous.get("sealed") is True:
            value = previous.get("value") or {}
            return {
                "generation_id": generation_id, "status": "complete",
                "page_count": 0,
                "copied_record_count": value.get("copied_record_count", 0),
                "skipped_tombstone_count": value.get("skipped_tombstone_count", 0),
                "collection_index": value.get("collection_index", 0),
            }

        value = previous.get("value") or {}
        if value:
            collections = value["collections"]
            collection_index = int(value.get("collection_index", 0))
            cursor = value.get("cursor")
            copied_record_count = int(value.get("copied_record_count", 0))
            skipped_tombstone_count = int(value.get("skipped_tombstone_count", 0))
        else:
            collections = []
            for collection in await self.vector_store.list_collections():
                collections.append({
                    "name": collection["name"],
                    "model": collection["model"],
                    "dimensions": collection["dimensions"],
                    "upper_bound": await self.vector_store.record_key_upper_bound(collection["name"]),
                })
            collection_index = 0
            cursor = None
            copied_record_count = 0
            skipped_tombstone_count = 0

        page_count = 0
        if collection_index < len(collections):
            collection = collections[collection_index]
            page = await self.vector_store.list_records_after(
                collection["name"], cursor, collection["upper_bound"], page_size,
            )
            to_copy = []
            for item in page:
                metadata = item.get("metadata") or {}
                source_id = metadata.get("source_id") or metadata.get("url")
                if not isinstance(source_id, str) or not source_id:
                    raise RuntimeError(
                        f"vector record {item['id']} in {collection['name']} has no source identity"
                    )
                source = await self.index_store.get_generation_source(
                    generation_id, source_id,
                )
                if source is None:
                    raise RuntimeError(
                        f"vector record {item['id']} references source {source_id} missing from generation Index"
                    )
                if source["tombstoned"]:
                    skipped_tombstone_count += 1
                    continue
                to_copy.append(item)
            if to_copy:
                await self.vector_store.upsert_generation(
                    generation_id, collection["name"], collection["model"],
                    collection["dimensions"], to_copy,
                )
                copied_record_count += len(to_copy)
            page_count = len(page)
            next_cursor = page[-1]["id"] if page else cursor
            collection_complete = (
                collection["upper_bound"] is None
                or next_cursor == collection["upper_bound"]
                or len(page) < page_size
            )
            if collection_complete:
                collection_index += 1
                cursor = None
            else:
                cursor = next_cursor

        complete = collection_index >= len(collections)
        checkpoint = {
            "collections": collections,
            "collection_index": collection_index,
            "cursor": cursor,
            "copied_record_count": copied_record_count,
            "skipped_tombstone_count": skipped_tombstone_count,
        }
        await self.index_store.record_generation_checkpoint(
            generation_id, "vectors", checkpoint, sealed=complete,
        )
        return {
            "generation_id": generation_id,
            "status": "complete" if complete else "in_progress",
            "page_count": page_count,
            "copied_record_count": copied_record_count,
            "skipped_tombstone_count": skipped_tombstone_count,
            "collection_index": collection_index,
            "collection_count": len(collections),
        }


    async def reconcile_index_changes_page(
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
        previous = (generation.get("checkpoints") or {}).get("index_reconciliation") or {}
        if previous.get("sealed") is True:
            value = previous.get("value") or {}
            return {
                "generation_id": generation_id, "status": "complete",
                "page_count": 0, "change_count": value.get("change_count", 0),
                "cursor": value.get("cursor", 0),
            }
        value = previous.get("value") or {}
        cursor = int(value.get("cursor", 0))
        change_count = int(value.get("change_count", 0))
        page = await self.index_store.list_generation_changes_after(
            generation_id, cursor, page_size,
        )
        for change in page:
            await self.index_store.put_generation_source(generation_id, {
                "source_id": change["source_id"],
                "high_sequence": change["high_sequence"],
                "revision_id": change["revision_id"],
                "profiles": change["profiles"],
                "tombstoned": change["tombstoned"],
                "updated_at": change["created_at"],
            })
        next_cursor = page[-1]["change_id"] if page else cursor
        change_count += len(page)
        high_watermark = await self.index_store.generation_change_high_watermark(generation_id)
        caught_up = next_cursor == high_watermark and len(page) < page_size
        checkpoint = {
            "cursor": next_cursor,
            "change_count": change_count,
            "observed_high_watermark": high_watermark,
        }
        await self.index_store.record_generation_checkpoint(
            generation_id, "index_reconciliation", checkpoint, sealed=False,
        )
        return {
            "generation_id": generation_id,
            "status": "caught_up" if caught_up else "in_progress",
            "page_count": len(page), "change_count": change_count,
            "cursor": next_cursor, "high_watermark": high_watermark,
        }

    async def fence_reconciled_generation(self, generation_id: str) -> dict[str, Any]:
        status = await self.index_store.generation_status()
        generation = next(
            (item for item in status["generations"] if item["generation_id"] == generation_id),
            None,
        )
        if generation is None or generation["state"] != "building":
            raise RuntimeError("generation is not in building state")
        checkpoint = (generation.get("checkpoints") or {}).get("index_reconciliation") or {}
        value = checkpoint.get("value") or {}
        cursor = int(value.get("cursor", 0))
        high_watermark = await self.index_store.generation_change_high_watermark(generation_id)
        if cursor != high_watermark:
            raise RuntimeError("generation change journal advanced; reconcile before fencing")
        fence = await self.index_store.acquire_generation_write_fence(
            generation_id, through_change_id=high_watermark,
        )
        try:
            await self.index_store.record_generation_checkpoint(
                generation_id, "index_reconciliation",
                {"cursor": high_watermark, "change_count": int(value.get("change_count", 0)),
                 "observed_high_watermark": high_watermark},
                sealed=True,
            )
        except Exception:
            await self.index_store.release_generation_write_fence(generation_id)
            raise
        return fence

