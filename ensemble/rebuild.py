"""Resumable generation-building operations.

The builder deliberately performs one bounded page per call. A caller can
schedule repeated calls, inspect the durable checkpoint, and resume after a
process restart without holding a long-lived transaction.
"""

from __future__ import annotations

import copy
from typing import Any


class IndexGenerationBuilder:
    def __init__(
        self, index_store: Any, document_store: Any | None = None,
        vector_store: Any | None = None, knowledge_store: Any | None = None,
        memory_store: Any | None = None, internal_client: Any | None = None,
    ):
        self.index_store = index_store
        self.internal_client = internal_client
        self.document_store = document_store
        self.vector_store = vector_store
        self.knowledge_store = knowledge_store
        self.memory_store = memory_store

    async def copy_index_sources(
        self, generation_id: str, *, page_size: int = 500, request: Any = None,
    ) -> dict[str, Any]:
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 10000:
            raise ValueError("page_size must be between 1 and 10000")

        status = (
            await self.internal_client.request("GET", "/api/v1/index/generations", request=request)
            if self.internal_client is not None else await self.index_store.generation_status()
        )
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
            upper_bound = (
                await self.internal_client.request(
                    "GET", f"/api/v1/index/generations/{generation_id}/source-upper-bound",
                    request=request,
                )
            ).get("upper_bound") if self.internal_client is not None else await self.index_store.source_key_upper_bound()
            cursor = None
            source_count = 0
            tombstone_count = 0

        page = (
            await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/sources/page",
                request=request,
                json={"cursor": cursor, "upper_bound": upper_bound, "limit": page_size},
            )
        ).get("sources", []) if self.internal_client is not None else await self.index_store.list_sources_after(
            cursor, upper_bound, page_size,
        )
        for source in page:
            if self.internal_client is not None:
                await self.internal_client.request(
                    "PUT", f"/api/v1/index/generations/{generation_id}/sources",
                    request=request, json={"source": source},
                )
            else:
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
        if self.internal_client is not None:
            await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/checkpoints",
                request=request,
                json={"capability": "index", "checkpoint": checkpoint, "sealed": complete},
            )
        else:
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
        self, generation_id: str, *, page_size: int = 500, request: Any = None,
    ) -> dict[str, Any]:
        if self.document_store is None:
            raise RuntimeError("document store is required for the document generation pass")
        if self.internal_client is not None:
            status = await self.internal_client.request(
                "GET", "/api/v1/index/generations", request=request,
            )
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
                    "generation_id": generation_id, "status": "complete", "page_count": 0,
                    "source_count": value.get("source_count", 0),
                    "document_count": value.get("document_count", 0),
                    "chunk_count": value.get("chunk_count", 0),
                    "tombstone_count": value.get("tombstone_count", 0),
                    "cursor": value.get("cursor"),
                    "upper_bound": value.get("source_upper_bound"),
                }
            prior = previous.get("value") or {}
            index_value = index_checkpoint.get("value") or {}
            upper_bound = index_value.get("source_upper_bound")
            cursor = prior.get("cursor")
            source_count = int(prior.get("source_count", 0))
            document_count = int(prior.get("document_count", 0))
            chunk_count = int(prior.get("chunk_count", 0))
            tombstone_count = int(prior.get("tombstone_count", 0))
            page = (await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/sources/page",
                request=request, json={"cursor": cursor, "upper_bound": upper_bound, "limit": page_size},
            )).get("sources", [])
            for source in page:
                result = await self.internal_client.request(
                    "POST", f"/api/v1/documents/generations/{generation_id}/sources",
                    request=request, json={"source": source},
                )
                if source.get("tombstoned"):
                    await self.internal_client.request(
                        "POST", f"/api/v1/vector/generations/{generation_id}/sources/tombstone",
                        request=request, json={"source_id": source["source_id"]},
                    )
                    tombstone_count += 1
                else:
                    document_count += result.get("document_count", 0)
                    chunk_count += result.get("chunk_count", 0)
            next_cursor = page[-1]["source_id"] if page else cursor
            source_count += len(page)
            complete = upper_bound is None or next_cursor == upper_bound or len(page) < page_size
            checkpoint = {
                "source_upper_bound": upper_bound, "cursor": next_cursor,
                "source_count": source_count, "document_count": document_count,
                "chunk_count": chunk_count, "tombstone_count": tombstone_count,
            }
            await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/checkpoints",
                request=request, json={"capability": "documents", "checkpoint": checkpoint, "sealed": complete},
            )
            return {
                "generation_id": generation_id,
                "status": "complete" if complete else "in_progress",
                "page_count": len(page), "source_count": source_count,
                "document_count": document_count, "chunk_count": chunk_count,
                "tombstone_count": tombstone_count, "cursor": next_cursor,
                "upper_bound": upper_bound,
            }

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
        self, generation_id: str, *, page_size: int = 500, request: Any = None,
    ) -> dict[str, Any]:
        if self.vector_store is None:
            raise RuntimeError("vector store is required for the vector generation pass")
        if self.internal_client is not None:
            status = await self.internal_client.request(
                "GET", "/api/v1/index/generations", request=request,
            )
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
                    "generation_id": generation_id, "status": "complete", "page_count": 0,
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
                collections = (await self.internal_client.request(
                    "GET", "/api/v1/vector/generations/collections", request=request,
                )).get("collections", [])
                collection_index = 0
                cursor = None
                copied_record_count = skipped_tombstone_count = 0
            page_count = 0
            if collection_index < len(collections):
                collection = collections[collection_index]
                page = (await self.internal_client.request(
                    "POST",
                    f"/api/v1/vector/generations/{generation_id}/collections/{collection['name']}/records/page",
                    request=request,
                    json={"cursor": cursor, "upper_bound": collection["upper_bound"], "limit": page_size},
                )).get("records", [])
                to_copy = []
                source_ids = []
                for item in page:
                    metadata = item.get("metadata") or {}
                    source_id = metadata.get("source_id") or metadata.get("url")
                    if not isinstance(source_id, str) or not source_id:
                        raise RuntimeError(f"vector record {item['id']} in {collection['name']} has no source identity")
                    source_ids.append(source_id)
                sources = {}
                if source_ids:
                    sources = (await self.internal_client.request(
                        "POST", f"/api/v1/index/generations/{generation_id}/sources/query",
                        request=request, json={"source_ids": list(dict.fromkeys(source_ids))},
                    )).get("sources", {})
                for item, source_id in zip(page, source_ids):
                    source = sources.get(source_id)
                    if source is None:
                        raise RuntimeError(
                            f"vector record {item['id']} references source {source_id} missing from generation Index"
                        )
                    if source.get("tombstoned"):
                        skipped_tombstone_count += 1
                    else:
                        to_copy.append(item)
                if to_copy:
                    await self.internal_client.request(
                        "POST",
                        f"/api/v1/vector/generations/{generation_id}/collections/{collection['name']}/records",
                        request=request,
                        json={
                            "model": collection["model"], "dimensions": collection["dimensions"],
                            "records": to_copy,
                        },
                    )
                    copied_record_count += len(to_copy)
                page_count = len(page)
                next_cursor = page[-1]["id"] if page else cursor
                collection_complete = (
                    collection["upper_bound"] is None or next_cursor == collection["upper_bound"]
                    or len(page) < page_size
                )
                if collection_complete:
                    collection_index += 1
                    cursor = None
                else:
                    cursor = next_cursor
            complete = collection_index >= len(collections)
            checkpoint = {
                "collections": collections, "collection_index": collection_index, "cursor": cursor,
                "copied_record_count": copied_record_count,
                "skipped_tombstone_count": skipped_tombstone_count,
            }
            await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/checkpoints",
                request=request, json={"capability": "vectors", "checkpoint": checkpoint, "sealed": complete},
            )
            return {
                "generation_id": generation_id, "status": "complete" if complete else "in_progress",
                "page_count": page_count, "copied_record_count": copied_record_count,
                "skipped_tombstone_count": skipped_tombstone_count,
                "collection_index": collection_index, "collection_count": len(collections),
            }

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
        self, generation_id: str, *, page_size: int = 500, request: Any = None,
    ) -> dict[str, Any]:
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 10000:
            raise ValueError("page_size must be between 1 and 10000")
        status = (
            await self.internal_client.request("GET", "/api/v1/index/generations", request=request)
            if self.internal_client is not None else await self.index_store.generation_status()
        )
        generation = next(
            (item for item in status["generations"] if item["generation_id"] == generation_id),
            None,
        )
        if generation is None or generation["state"] != "building":
            raise RuntimeError("generation is not in building state")
        checkpoints = generation.get("checkpoints") or {}
        if (checkpoints.get("index") or {}).get("sealed") is not True:
            raise RuntimeError("Index source copy must be sealed before journal reconciliation")
        previous = checkpoints.get("index_reconciliation") or {}
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
        page = (
            await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/changes/page",
                request=request, json={"after_change_id": cursor, "limit": page_size},
            )
        ).get("changes", []) if self.internal_client is not None else await self.index_store.list_generation_changes_after(
            generation_id, cursor, page_size,
        )
        pending_change = next((index for index, change in enumerate(page)
                               if not change.get("acknowledged", False)), None)
        ready_page = page if pending_change is None else page[:pending_change]
        for change in ready_page:
            source = {
                "source_id": change["source_id"],
                "high_sequence": change["high_sequence"],
                "revision_id": change["revision_id"],
                "profiles": change["profiles"],
                "tombstoned": change["tombstoned"],
                "updated_at": change["created_at"],
            }
            if self.internal_client is not None:
                await self.internal_client.request(
                    "PUT", f"/api/v1/index/generations/{generation_id}/sources",
                    request=request, json={"source": source},
                )
            else:
                await self.index_store.put_generation_source(generation_id, source)

        next_cursor = ready_page[-1]["change_id"] if ready_page else cursor
        change_count += len(ready_page)
        high_watermark = (
            await self.internal_client.request(
                "GET", f"/api/v1/index/generations/{generation_id}/changes/high-watermark",
                request=request,
            )
        ).get("high_watermark", 0) if self.internal_client is not None else await self.index_store.generation_change_high_watermark(generation_id)
        waiting_for_ack = pending_change is not None
        caught_up = not waiting_for_ack and next_cursor == high_watermark and len(page) < page_size
        checkpoint = {
            "cursor": next_cursor,
            "change_count": change_count,
            "observed_high_watermark": high_watermark,
        }
        if self.internal_client is not None:
            await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/checkpoints",
                request=request,
                json={"capability": "index_reconciliation", "checkpoint": checkpoint, "sealed": False},
            )
        else:
            await self.index_store.record_generation_checkpoint(
                generation_id, "index_reconciliation", checkpoint, sealed=False,
            )
        return {
            "generation_id": generation_id,
            "status": "waiting_for_ack" if waiting_for_ack else ("caught_up" if caught_up else "in_progress"),
            "page_count": len(ready_page), "pending_change_id": page[pending_change]["change_id"] if waiting_for_ack else None,
            "change_count": change_count, "cursor": next_cursor,
            "high_watermark": high_watermark,
        }

    async def copy_memory_page(
        self, generation_id: str, *, page_size: int = 500, request: Any = None,
    ) -> dict[str, Any]:
        if self.memory_store is None:
            raise RuntimeError("Memory store is required for the Memory generation pass")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 10000:
            raise ValueError("page_size must be between 1 and 10000")

        if self.internal_client is not None:
            status = await self.internal_client.request("GET", "/api/v1/index/generations", request=request)
            generation = next((item for item in status["generations"] if item["generation_id"] == generation_id), None)
            if generation is None or generation["state"] != "building":
                raise RuntimeError("generation is not in building state")
            previous = (generation.get("checkpoints") or {}).get("memory") or {}
            if previous.get("sealed") is True:
                value = previous.get("value") or {}
                return {"generation_id": generation_id, "status": "complete", "page_count": 0,
                        "record_count": value.get("record_count", 0),
                        "cursor": value.get("cursor"), "upper_bound": value.get("upper_bound")}
            value = previous.get("value") or {}
            upper_bound = value.get("upper_bound") if value else (
                await self.internal_client.request("GET", "/api/v1/memory/generations/source-upper-bound", request=request)
            ).get("upper_bound")
            cursor = value.get("cursor") if value else None
            record_count = int(value.get("record_count", 0))
            names = (await self.internal_client.request(
                "POST", "/api/v1/memory/generations/sources/page", request=request,
                json={"cursor": cursor, "upper_bound": upper_bound, "limit": page_size},
            )).get("names", [])
            snapshots = {}
            if names:
                snapshots = (await self.internal_client.request(
                    "POST", "/api/v1/memory/generations/snapshots/query", request=request,
                    json={"names": names},
                )).get("snapshots", {})
            for name in names:
                snapshot = snapshots.get(name)
                if snapshot is not None:
                    await self.internal_client.request(
                        "PUT", f"/api/v1/memory/generations/{generation_id}/snapshots/{name}",
                        request=request, json={"snapshot": snapshot},
                    )
                    record_count += 1
            next_cursor = names[-1] if names else cursor
            complete = upper_bound is None or next_cursor == upper_bound or len(names) < page_size
            checkpoint = {"cursor": next_cursor, "upper_bound": upper_bound, "record_count": record_count}
            await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/checkpoints", request=request,
                json={"capability": "memory", "checkpoint": checkpoint, "sealed": complete},
            )
            return {"generation_id": generation_id, "status": "complete" if complete else "in_progress",
                    "page_count": len(names), "record_count": record_count,
                    "cursor": next_cursor, "upper_bound": upper_bound}

        status = await self.index_store.generation_status()
        generation = next((item for item in status["generations"] if item["generation_id"] == generation_id), None)
        if generation is None or generation["state"] != "building":
            raise RuntimeError("generation is not in building state")
        checkpoints = generation.get("checkpoints") or {}
        previous = checkpoints.get("memory") or {}
        if previous.get("sealed") is True:
            value = previous.get("value") or {}
            return {"generation_id": generation_id, "status": "complete",
                    "page_count": 0, "record_count": value.get("record_count", 0),
                    "cursor": value.get("cursor"), "upper_bound": value.get("upper_bound")}
        value = previous.get("value") or {}
        upper_bound = value.get("upper_bound") if value else await self.memory_store.memory_generation_upper_bound()
        cursor = value.get("cursor") if value else None
        record_count = int(value.get("record_count", 0))
        names = await self.memory_store.list_memory_names_after(cursor, upper_bound, page_size)
        for name in names:
            snapshot = await self.memory_store.get_memory_snapshot(name)
            if snapshot is not None:
                await self.memory_store.put_memory_generation_snapshot(generation_id, name, snapshot)
                record_count += 1
        next_cursor = names[-1] if names else cursor
        complete = upper_bound is None or next_cursor == upper_bound or len(names) < page_size
        checkpoint = {"cursor": next_cursor, "upper_bound": upper_bound, "record_count": record_count}
        await self.index_store.record_generation_checkpoint(generation_id, "memory", checkpoint, sealed=complete)
        return {"generation_id": generation_id, "status": "complete" if complete else "in_progress",
                "page_count": len(names), "record_count": record_count,
                "cursor": next_cursor, "upper_bound": upper_bound}

    async def reconcile_memory_changes_page(
        self, generation_id: str, *, page_size: int = 250, request: Any = None,
    ) -> dict[str, Any]:
        if self.memory_store is None:
            raise RuntimeError("Memory store is required for Memory reconciliation")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 10000:
            raise ValueError("page_size must be between 1 and 10000")

        if self.internal_client is not None:
            status = await self.internal_client.request("GET", "/api/v1/index/generations", request=request)
            generation = next((item for item in status["generations"] if item["generation_id"] == generation_id), None)
            if generation is None or generation["state"] != "building":
                raise RuntimeError("generation is not in building state")
            checkpoints = generation.get("checkpoints") or {}
            if (checkpoints.get("memory") or {}).get("sealed") is not True:
                raise RuntimeError("Memory source copy must be sealed before reconciliation")
            previous = checkpoints.get("memory_reconciliation") or {}
            if previous.get("sealed") is True:
                value = previous.get("value") or {}
                return {"generation_id": generation_id, "status": "complete", "page_count": 0,
                        "change_count": value.get("change_count", 0), "cursor": value.get("cursor", 0)}
            value = previous.get("value") or {}
            cursor = int(value.get("cursor", 0))
            change_count = int(value.get("change_count", 0))
            page = (await self.internal_client.request(
                "POST", f"/api/v1/memory/generations/{generation_id}/changes/page",
                request=request, json={"after_change_id": cursor, "limit": page_size},
            )).get("changes", [])
            for change in page:
                await self.internal_client.request(
                    "PUT", f"/api/v1/memory/generations/{generation_id}/snapshots/{change['name']}",
                    request=request, json={"snapshot": change["snapshot"]},
                )
            next_cursor = page[-1]["change_id"] if page else cursor
            if page:
                await self.internal_client.request(
                    "POST", f"/api/v1/memory/generations/{generation_id}/changes/acknowledgements",
                    request=request, json={"through_change_id": next_cursor},
                )
            change_count += len(page)
            high_watermark = (await self.internal_client.request(
                "GET", f"/api/v1/memory/generations/{generation_id}/changes/high-watermark",
                request=request,
            )).get("high_watermark", 0)
            caught_up = next_cursor == high_watermark and len(page) < page_size
            checkpoint = {"cursor": next_cursor, "change_count": change_count,
                          "observed_high_watermark": high_watermark}
            await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/checkpoints", request=request,
                json={"capability": "memory_reconciliation", "checkpoint": checkpoint, "sealed": False},
            )
            return {"generation_id": generation_id,
                    "status": "caught_up" if caught_up else "in_progress",
                    "page_count": len(page), "change_count": change_count,
                    "cursor": next_cursor, "high_watermark": high_watermark}

        status = await self.index_store.generation_status()
        generation = next((item for item in status["generations"] if item["generation_id"] == generation_id), None)
        if generation is None or generation["state"] != "building":
            raise RuntimeError("generation is not in building state")
        checkpoints = generation.get("checkpoints") or {}
        if (checkpoints.get("memory") or {}).get("sealed") is not True:
            raise RuntimeError("Memory source copy must be sealed before reconciliation")
        previous = checkpoints.get("memory_reconciliation") or {}
        if previous.get("sealed") is True:
            value = previous.get("value") or {}
            return {"generation_id": generation_id, "status": "complete",
                    "page_count": 0, "change_count": value.get("change_count", 0),
                    "cursor": value.get("cursor", 0)}
        value = previous.get("value") or {}
        cursor = int(value.get("cursor", 0))
        change_count = int(value.get("change_count", 0))
        page = await self.memory_store.list_memory_generation_changes_after(generation_id, cursor, page_size)
        for change in page:
            await self.memory_store.put_memory_generation_snapshot(
                generation_id, change["name"], change["snapshot"],
            )
        next_cursor = page[-1]["change_id"] if page else cursor
        if page:
            await self.memory_store.acknowledge_memory_generation_changes(generation_id, next_cursor)
        change_count += len(page)
        high_watermark = await self.memory_store.memory_generation_change_high_watermark(generation_id)
        caught_up = next_cursor == high_watermark and len(page) < page_size
        checkpoint = {"cursor": next_cursor, "change_count": change_count,
                      "observed_high_watermark": high_watermark}
        await self.index_store.record_generation_checkpoint(
            generation_id, "memory_reconciliation", checkpoint, sealed=False,
        )
        return {"generation_id": generation_id,
                "status": "caught_up" if caught_up else "in_progress",
                "page_count": len(page), "change_count": change_count,
                "cursor": next_cursor, "high_watermark": high_watermark}

    async def fence_reconciled_generation(self, generation_id: str, request: Any = None) -> dict[str, Any]:
        if self.internal_client is not None:
            status = await self.internal_client.request("GET", "/api/v1/index/generations", request=request)
            generation = next(
                (item for item in status["generations"] if item["generation_id"] == generation_id),
                None,
            )
            if generation is None or generation["state"] != "building":
                raise RuntimeError("generation is not in building state")
            checkpoints = generation.get("checkpoints") or {}
            base_capabilities = ("index", "documents", "vectors", "memory", "knowledge")
            reconciliation_capabilities = (
                "index_reconciliation", "documents_reconciliation", "vectors_reconciliation",
                "memory_reconciliation", "knowledge_reconciliation",
            )
            for capability in base_capabilities:
                if (checkpoints.get(capability) or {}).get("sealed") is not True:
                    raise RuntimeError(f"{capability} source copy must be sealed before final write fencing")
            high_watermark = (await self.internal_client.request(
                "GET", f"/api/v1/index/generations/{generation_id}/changes/high-watermark", request=request,
            )).get("high_watermark", 0)
            memory_high_watermark = (await self.internal_client.request(
                "GET", f"/api/v1/memory/generations/{generation_id}/changes/high-watermark", request=request,
            )).get("high_watermark", 0)
            reconciliation_values = {}
            for capability in reconciliation_capabilities:
                checkpoint = checkpoints.get(capability) or {}
                value = checkpoint.get("value") or {}
                cursor = int(value.get("cursor", 0))
                expected = memory_high_watermark if capability == "memory_reconciliation" else high_watermark
                if cursor != expected:
                    raise RuntimeError(f"{capability} is not caught up to its generation change journal")
                reconciliation_values[capability] = (value, expected)
            pending_memory = (await self.internal_client.request(
                "GET", f"/api/v1/memory/generations/{generation_id}/changes/pending", request=request,
            )).get("pending", 0)
            if pending_memory:
                raise RuntimeError("unacknowledged Memory changes remain; reconcile before fencing")
            fence = await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/write-fence/acquire",
                request=request, json={"through_change_id": high_watermark},
            )
            try:
                memory_after_fence = (await self.internal_client.request(
                    "GET", f"/api/v1/memory/generations/{generation_id}/changes/high-watermark",
                )).get("high_watermark", 0)
                pending_after_fence = (await self.internal_client.request(
                    "GET", f"/api/v1/memory/generations/{generation_id}/changes/pending",
                )).get("pending", 0)
                expected_memory = reconciliation_values["memory_reconciliation"][1]
                if memory_after_fence != expected_memory or pending_after_fence:
                    raise RuntimeError("Memory changed while acquiring the write fence; release and reconcile again")
                for capability, (value, expected) in reconciliation_values.items():
                    await self.internal_client.request(
                        "POST", f"/api/v1/index/generations/{generation_id}/checkpoints",
                        request=request, json={
                            "capability": capability,
                            "checkpoint": {**value, "cursor": expected, "observed_high_watermark": expected},
                            "sealed": True,
                        },
                    )
            except Exception:
                await self.internal_client.request(
                    "POST", f"/api/v1/index/generations/{generation_id}/write-fence/release", request=request,
                )
                raise
            return fence

        status = await self.index_store.generation_status()
        generation = next(
            (item for item in status["generations"] if item["generation_id"] == generation_id),
            None,
        )
        if generation is None or generation["state"] != "building":
            raise RuntimeError("generation is not in building state")
        checkpoints = generation.get("checkpoints") or {}
        base_capabilities = ("index", "documents", "vectors", "memory", "knowledge")
        reconciliation_capabilities = (
            "index_reconciliation", "documents_reconciliation", "vectors_reconciliation",
            "memory_reconciliation", "knowledge_reconciliation",
        )
        for capability in base_capabilities:
            if (checkpoints.get(capability) or {}).get("sealed") is not True:
                raise RuntimeError(f"{capability} source copy must be sealed before final write fencing")
        high_watermark = await self.index_store.generation_change_high_watermark(generation_id)
        memory_high_watermark = await self.memory_store.memory_generation_change_high_watermark(generation_id)
        reconciliation_values = {}
        for capability in reconciliation_capabilities:
            checkpoint = checkpoints.get(capability) or {}
            value = checkpoint.get("value") or {}
            cursor = int(value.get("cursor", 0))
            expected = memory_high_watermark if capability == "memory_reconciliation" else high_watermark
            if cursor != expected:
                raise RuntimeError(f"{capability} is not caught up to its generation change journal")
            reconciliation_values[capability] = (value, expected)
        pending_memory = await self.memory_store.pending_memory_generation_changes(generation_id)
        if pending_memory:
            raise RuntimeError("unacknowledged Memory changes remain; reconcile before fencing")
        fence = await self.index_store.acquire_generation_write_fence(
            generation_id, through_change_id=high_watermark,
        )
        try:
            # Recheck Memory after acquiring the shared fence. A Memory write
            # that began just before acquisition may have committed meanwhile.
            memory_after_fence = await self.memory_store.memory_generation_change_high_watermark(generation_id)
            pending_after_fence = await self.memory_store.pending_memory_generation_changes(generation_id)
            expected_memory = reconciliation_values["memory_reconciliation"][1]
            if memory_after_fence != expected_memory or pending_after_fence:
                raise RuntimeError("Memory changed while acquiring the write fence; release and reconcile again")
            for capability, (value, expected) in reconciliation_values.items():
                await self.index_store.record_generation_checkpoint(
                    generation_id, capability,
                    {**value, "cursor": expected, "observed_high_watermark": expected},
                    sealed=True,
                )
        except Exception:
            await self.index_store.release_generation_write_fence(generation_id)
            raise
        return fence

    async def reconcile_document_changes_page(
        self, generation_id: str, *, page_size: int = 250, request: Any = None,
    ) -> dict[str, Any]:
        if self.document_store is None:
            raise RuntimeError("document store is required for document reconciliation")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 10000:
            raise ValueError("page_size must be between 1 and 10000")

        if self.internal_client is not None:
            status = await self.internal_client.request("GET", "/api/v1/index/generations", request=request)
            generation = next((item for item in status["generations"] if item["generation_id"] == generation_id), None)
            if generation is None or generation["state"] != "building":
                raise RuntimeError("generation is not in building state")
            checkpoints = generation.get("checkpoints") or {}
            if (checkpoints.get("documents") or {}).get("sealed") is not True:
                raise RuntimeError("document source copy must be sealed before document reconciliation")
            index_cursor = int(((checkpoints.get("index_reconciliation") or {}).get("value") or {}).get("cursor", 0))
            previous = checkpoints.get("documents_reconciliation") or {}
            if previous.get("sealed") is True:
                value = previous.get("value") or {}
                return {"generation_id": generation_id, "status": "complete", "page_count": 0,
                        "change_count": value.get("change_count", 0), "cursor": value.get("cursor", 0)}
            value = previous.get("value") or {}
            cursor = int(value.get("cursor", 0))
            change_count = int(value.get("change_count", 0))
            page = (await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/changes/page",
                request=request, json={"after_change_id": cursor, "limit": page_size},
            )).get("changes", [])
            pending = next((i for i, change in enumerate(page) if not change.get("acknowledged", False)), None)
            ready_page = page if pending is None else page[:pending]
            for change in ready_page:
                if change["change_id"] > index_cursor:
                    raise RuntimeError("Index journal reconciliation must advance before Documents")
                source_id = change["source_id"]
                source = (await self.internal_client.request(
                    "POST", f"/api/v1/index/generations/{generation_id}/sources/query",
                    request=request, json={"source_ids": [source_id]},
                )).get("sources", {}).get(source_id)
                if source is None:
                    raise RuntimeError("reconciled Index source is missing from the target generation")
                await self.internal_client.request(
                    "POST", f"/api/v1/documents/generations/{generation_id}/sources",
                    request=request, json={"source": source},
                )
            next_cursor = ready_page[-1]["change_id"] if ready_page else cursor
            change_count += len(ready_page)
            high_watermark = (await self.internal_client.request(
                "GET", f"/api/v1/index/generations/{generation_id}/changes/high-watermark", request=request,
            )).get("high_watermark", 0)
            waiting = pending is not None
            caught_up = not waiting and next_cursor == high_watermark and len(page) < page_size
            checkpoint = {"cursor": next_cursor, "change_count": change_count,
                          "observed_high_watermark": high_watermark}
            await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/checkpoints", request=request,
                json={"capability": "documents_reconciliation", "checkpoint": checkpoint, "sealed": False},
            )
            return {"generation_id": generation_id,
                    "status": "waiting_for_ack" if waiting else ("caught_up" if caught_up else "in_progress"),
                    "page_count": len(ready_page),
                    "pending_change_id": page[pending]["change_id"] if waiting else None,
                    "change_count": change_count, "cursor": next_cursor,
                    "high_watermark": high_watermark}

        status = await self.index_store.generation_status()
        generation = next((item for item in status["generations"] if item["generation_id"] == generation_id), None)
        if generation is None or generation["state"] != "building":
            raise RuntimeError("generation is not in building state")
        checkpoints = generation.get("checkpoints") or {}
        if (checkpoints.get("documents") or {}).get("sealed") is not True:
            raise RuntimeError("document source copy must be sealed before document reconciliation")
        index_cursor = int(((checkpoints.get("index_reconciliation") or {}).get("value") or {}).get("cursor", 0))
        previous = checkpoints.get("documents_reconciliation") or {}
        if previous.get("sealed") is True:
            value = previous.get("value") or {}
            return {"generation_id": generation_id, "status": "complete",
                    "page_count": 0, "change_count": value.get("change_count", 0),
                    "cursor": value.get("cursor", 0)}
        value = previous.get("value") or {}
        cursor = int(value.get("cursor", 0))
        change_count = int(value.get("change_count", 0))
        page = await self.index_store.list_generation_changes_after(generation_id, cursor, page_size)
        pending_change = next((i for i, change in enumerate(page) if not change.get("acknowledged", False)), None)
        ready_page = page if pending_change is None else page[:pending_change]
        active_chunks = None
        for change in ready_page:
            if change["change_id"] > index_cursor:
                raise RuntimeError("Index journal reconciliation must advance before Documents")
            source = await self.index_store.get_generation_source(generation_id, change["source_id"])
            if source is None:
                raise RuntimeError("reconciled Index source is missing from the target generation")
            if source["tombstoned"]:
                await self.document_store.tombstone_generation_source(generation_id, change["source_id"])
                continue
            if active_chunks is None:
                active_chunks = await self.document_store.all_chunks()
            source_chunks = [item for item in active_chunks if item.document.source_id == change["source_id"]]
            if not source_chunks:
                raise RuntimeError("acknowledged source has no active document chunks")
            revisions = {item.document.revision for item in source_chunks}
            if revisions != {source["high_sequence"]}:
                raise RuntimeError("active document revision does not match the reconciled Index source")
            grouped: dict[str, tuple[Any, list[Any]]] = {}
            for item in source_chunks:
                key = str(item.document.id)
                if key not in grouped:
                    grouped[key] = (item.document, [])
                grouped[key][1].append(item.chunk)
            for document, chunks in grouped.values():
                await self.document_store.upsert_generation(
                    generation_id, copy.deepcopy(document), copy.deepcopy(chunks),
                )
        next_cursor = ready_page[-1]["change_id"] if ready_page else cursor
        change_count += len(ready_page)
        high_watermark = await self.index_store.generation_change_high_watermark(generation_id)
        waiting = pending_change is not None
        caught_up = not waiting and next_cursor == high_watermark and len(page) < page_size
        checkpoint = {"cursor": next_cursor, "change_count": change_count,
                      "observed_high_watermark": high_watermark}
        await self.index_store.record_generation_checkpoint(
            generation_id, "documents_reconciliation", checkpoint, sealed=False,
        )
        return {"generation_id": generation_id,
                "status": "waiting_for_ack" if waiting else ("caught_up" if caught_up else "in_progress"),
                "page_count": len(ready_page),
                "pending_change_id": page[pending_change]["change_id"] if waiting else None,
                "change_count": change_count, "cursor": next_cursor,
                "high_watermark": high_watermark}

    async def reconcile_vector_changes_page(
        self, generation_id: str, *, page_size: int = 250, request: Any = None,
    ) -> dict[str, Any]:
        if self.vector_store is None:
            raise RuntimeError("vector store is required for vector reconciliation")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 10000:
            raise ValueError("page_size must be between 1 and 10000")

        if self.internal_client is not None:
            status = await self.internal_client.request("GET", "/api/v1/index/generations", request=request)
            generation = next((item for item in status["generations"] if item["generation_id"] == generation_id), None)
            if generation is None or generation["state"] != "building":
                raise RuntimeError("generation is not in building state")
            checkpoints = generation.get("checkpoints") or {}
            if (checkpoints.get("vectors") or {}).get("sealed") is not True:
                raise RuntimeError("vector source copy must be sealed before vector reconciliation")
            index_cursor = int(((checkpoints.get("index_reconciliation") or {}).get("value") or {}).get("cursor", 0))
            previous = checkpoints.get("vectors_reconciliation") or {}
            if previous.get("sealed") is True:
                value = previous.get("value") or {}
                return {"generation_id": generation_id, "status": "complete", "page_count": 0,
                        "change_count": value.get("change_count", 0), "cursor": value.get("cursor", 0)}
            value = previous.get("value") or {}
            cursor = int(value.get("cursor", 0))
            change_count = int(value.get("change_count", 0))
            page = (await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/changes/page",
                request=request, json={"after_change_id": cursor, "limit": page_size},
            )).get("changes", [])
            pending = next((i for i, change in enumerate(page) if not change.get("acknowledged", False)), None)
            ready_page = page if pending is None else page[:pending]
            for change in ready_page:
                if change["change_id"] > index_cursor:
                    raise RuntimeError("Index journal reconciliation must advance before Vectors")
                source_id = change["source_id"]
                source = (await self.internal_client.request(
                    "POST", f"/api/v1/index/generations/{generation_id}/sources/query",
                    request=request, json={"source_ids": [source_id]},
                )).get("sources", {}).get(source_id)
                if source is None:
                    raise RuntimeError("reconciled Index source is missing from the target generation")
                await self.internal_client.request(
                    "POST", f"/api/v1/vector/generations/{generation_id}/sources/reconcile",
                    request=request,
                    json={"source_id": source_id, "tombstoned": source.get("tombstoned", False)},
                )
            next_cursor = ready_page[-1]["change_id"] if ready_page else cursor
            change_count += len(ready_page)
            high_watermark = (await self.internal_client.request(
                "GET", f"/api/v1/index/generations/{generation_id}/changes/high-watermark", request=request,
            )).get("high_watermark", 0)
            waiting = pending is not None
            caught_up = not waiting and next_cursor == high_watermark and len(page) < page_size
            checkpoint = {"cursor": next_cursor, "change_count": change_count,
                          "observed_high_watermark": high_watermark}
            await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/checkpoints", request=request,
                json={"capability": "vectors_reconciliation", "checkpoint": checkpoint, "sealed": False},
            )
            return {"generation_id": generation_id,
                    "status": "waiting_for_ack" if waiting else ("caught_up" if caught_up else "in_progress"),
                    "page_count": len(ready_page),
                    "pending_change_id": page[pending]["change_id"] if waiting else None,
                    "change_count": change_count, "cursor": next_cursor,
                    "high_watermark": high_watermark}

        status = await self.index_store.generation_status()
        generation = next((item for item in status["generations"] if item["generation_id"] == generation_id), None)
        if generation is None or generation["state"] != "building":
            raise RuntimeError("generation is not in building state")
        checkpoints = generation.get("checkpoints") or {}
        if (checkpoints.get("vectors") or {}).get("sealed") is not True:
            raise RuntimeError("vector source copy must be sealed before vector reconciliation")
        index_cursor = int(((checkpoints.get("index_reconciliation") or {}).get("value") or {}).get("cursor", 0))
        previous = checkpoints.get("vectors_reconciliation") or {}
        if previous.get("sealed") is True:
            value = previous.get("value") or {}
            return {"generation_id": generation_id, "status": "complete",
                    "page_count": 0, "change_count": value.get("change_count", 0),
                    "cursor": value.get("cursor", 0)}
        value = previous.get("value") or {}
        cursor = int(value.get("cursor", 0))
        change_count = int(value.get("change_count", 0))
        page = await self.index_store.list_generation_changes_after(generation_id, cursor, page_size)
        pending_change = next((i for i, change in enumerate(page) if not change.get("acknowledged", False)), None)
        ready_page = page if pending_change is None else page[:pending_change]
        collections = await self.vector_store.list_collections()
        for change in ready_page:
            if change["change_id"] > index_cursor:
                raise RuntimeError("Index journal reconciliation must advance before Vectors")
            source = await self.index_store.get_generation_source(generation_id, change["source_id"])
            if source is None:
                raise RuntimeError("reconciled Index source is missing from the target generation")
            if source["tombstoned"]:
                await self.vector_store.tombstone_generation_source(generation_id, change["source_id"])
                continue
            for collection in collections:
                name = collection["name"]
                records = await self.vector_store.list_records_for_source(name, change["source_id"])
                await self.vector_store.delete_generation_source(generation_id, change["source_id"], name=name)
                if records:
                    await self.vector_store.upsert_generation(
                        generation_id, name, collection["model"], collection["dimensions"], records,
                    )
        next_cursor = ready_page[-1]["change_id"] if ready_page else cursor
        change_count += len(ready_page)
        high_watermark = await self.index_store.generation_change_high_watermark(generation_id)
        waiting = pending_change is not None
        caught_up = not waiting and next_cursor == high_watermark and len(page) < page_size
        checkpoint = {"cursor": next_cursor, "change_count": change_count,
                      "observed_high_watermark": high_watermark}
        await self.index_store.record_generation_checkpoint(
            generation_id, "vectors_reconciliation", checkpoint, sealed=False,
        )
        return {"generation_id": generation_id,
                "status": "waiting_for_ack" if waiting else ("caught_up" if caught_up else "in_progress"),
                "page_count": len(ready_page),
                "pending_change_id": page[pending_change]["change_id"] if waiting else None,
                "change_count": change_count, "cursor": next_cursor,
                "high_watermark": high_watermark}



    async def copy_knowledge_page(
        self, generation_id: str, *, page_size: int = 500, request: Any = None,
    ) -> dict[str, Any]:
        if self.knowledge_store is None:
            raise RuntimeError("Knowledge store is required for the generation pass")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 10000:
            raise ValueError("page_size must be between 1 and 10000")

        if self.internal_client is not None:
            status = await self.internal_client.request("GET", "/api/v1/index/generations", request=request)
            generation = next((item for item in status["generations"] if item["generation_id"] == generation_id), None)
            if generation is None or generation["state"] != "building":
                raise RuntimeError("generation is not in building state")
            checkpoints = generation.get("checkpoints") or {}
            index_checkpoint = checkpoints.get("index") or {}
            if index_checkpoint.get("sealed") is not True:
                raise RuntimeError("Index source copy must be sealed before Knowledge copy")
            previous = checkpoints.get("knowledge") or {}
            if previous.get("sealed") is True:
                value = previous.get("value") or {}
                return {"generation_id": generation_id, "status": "complete", "page_count": 0,
                        "source_count": value.get("source_count", 0),
                        "record_count": value.get("record_count", 0),
                        "tombstone_count": value.get("tombstone_count", 0),
                        "cursor": value.get("cursor")}
            prior = previous.get("value") or {}
            upper_bound = (index_checkpoint.get("value") or {}).get("source_upper_bound")
            cursor = prior.get("cursor")
            source_count = int(prior.get("source_count", 0))
            record_count = int(prior.get("record_count", 0))
            tombstone_count = int(prior.get("tombstone_count", 0))
            page = (await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/sources/page",
                request=request, json={"cursor": cursor, "upper_bound": upper_bound, "limit": page_size},
            )).get("sources", [])
            for source in page:
                source_id = source["source_id"]
                if source.get("tombstoned"):
                    await self.internal_client.request(
                        "POST", f"/api/v1/knowledge/generations/{generation_id}/records",
                        request=request, json={"source_id": source_id, "tombstoned": True},
                    )
                    tombstone_count += 1
                    continue
                record = (await self.internal_client.request(
                    "POST", "/api/v1/knowledge/records/query",
                    request=request, json={"source_ids": [source_id]},
                )).get("records", {}).get(source_id)
                if record is None:
                    continue
                if record.get("tombstoned"):
                    await self.internal_client.request(
                        "POST", f"/api/v1/knowledge/generations/{generation_id}/records",
                        request=request, json={"source_id": source_id, "tombstoned": True},
                    )
                    tombstone_count += 1
                    continue
                if record["revision_sequence"] != source["high_sequence"]:
                    raise RuntimeError("active Knowledge revision does not match the Index source")
                await self.internal_client.request(
                    "POST", f"/api/v1/knowledge/generations/{generation_id}/records",
                    request=request, json={"source_id": source_id, "record": record},
                )
                record_count += 1
            next_cursor = page[-1]["source_id"] if page else cursor
            source_count += len(page)
            complete = upper_bound is None or next_cursor == upper_bound or len(page) < page_size
            checkpoint = {"cursor": next_cursor, "source_upper_bound": upper_bound,
                          "source_count": source_count, "record_count": record_count,
                          "tombstone_count": tombstone_count}
            await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/checkpoints", request=request,
                json={"capability": "knowledge", "checkpoint": checkpoint, "sealed": complete},
            )
            return {"generation_id": generation_id, "status": "complete" if complete else "in_progress",
                    "page_count": len(page), "source_count": source_count,
                    "record_count": record_count, "tombstone_count": tombstone_count,
                    "cursor": next_cursor, "upper_bound": upper_bound}

        status = await self.index_store.generation_status()
        generation = next((item for item in status["generations"] if item["generation_id"] == generation_id), None)
        if generation is None or generation["state"] != "building":
            raise RuntimeError("generation is not in building state")
        checkpoints = generation.get("checkpoints") or {}
        index_checkpoint = checkpoints.get("index") or {}
        if index_checkpoint.get("sealed") is not True:
            raise RuntimeError("Index source copy must be sealed before Knowledge copy")
        previous = checkpoints.get("knowledge") or {}
        if previous.get("sealed") is True:
            value = previous.get("value") or {}
            return {"generation_id": generation_id, "status": "complete",
                    "page_count": 0, "source_count": value.get("source_count", 0),
                    "record_count": value.get("record_count", 0),
                    "tombstone_count": value.get("tombstone_count", 0),
                    "cursor": value.get("cursor")}
        prior_value = previous.get("value") or {}
        index_value = index_checkpoint.get("value") or {}
        upper_bound = index_value.get("source_upper_bound")
        cursor = prior_value.get("cursor")
        source_count = int(prior_value.get("source_count", 0))
        record_count = int(prior_value.get("record_count", 0))
        tombstone_count = int(prior_value.get("tombstone_count", 0))
        page = await self.index_store.list_generation_sources_after(
            generation_id, cursor, upper_bound, page_size,
        )
        for source in page:
            source_id = source["source_id"]
            if source["tombstoned"]:
                await self.knowledge_store.tombstone_generation_source(generation_id, source_id)
                tombstone_count += 1
                continue
            record = await self.knowledge_store.get(source_id)
            if record is None:
                continue
            if record.get("tombstoned"):
                await self.knowledge_store.tombstone_generation_source(generation_id, source_id)
                tombstone_count += 1
                continue
            if record["revision_sequence"] != source["high_sequence"]:
                raise RuntimeError("active Knowledge revision does not match the Index source")
            await self.knowledge_store.upsert_generation(generation_id, record)
            record_count += 1
        next_cursor = page[-1]["source_id"] if page else cursor
        source_count += len(page)
        complete = upper_bound is None or next_cursor == upper_bound or len(page) < page_size
        checkpoint = {"cursor": next_cursor, "source_upper_bound": upper_bound,
                      "source_count": source_count, "record_count": record_count,
                      "tombstone_count": tombstone_count}
        await self.index_store.record_generation_checkpoint(
            generation_id, "knowledge", checkpoint, sealed=complete,
        )
        return {"generation_id": generation_id, "status": "complete" if complete else "in_progress",
                "page_count": len(page), "source_count": source_count,
                "record_count": record_count, "tombstone_count": tombstone_count,
                "cursor": next_cursor, "upper_bound": upper_bound}

    async def reconcile_knowledge_changes_page(
        self, generation_id: str, *, page_size: int = 250, request: Any = None,
    ) -> dict[str, Any]:
        if self.knowledge_store is None:
            raise RuntimeError("Knowledge store is required for reconciliation")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 10000:
            raise ValueError("page_size must be between 1 and 10000")

        if self.internal_client is not None:
            status = await self.internal_client.request("GET", "/api/v1/index/generations", request=request)
            generation = next((item for item in status["generations"] if item["generation_id"] == generation_id), None)
            if generation is None or generation["state"] != "building":
                raise RuntimeError("generation is not in building state")
            checkpoints = generation.get("checkpoints") or {}
            if (checkpoints.get("knowledge") or {}).get("sealed") is not True:
                raise RuntimeError("Knowledge source copy must be sealed before reconciliation")
            index_cursor = int(((checkpoints.get("index_reconciliation") or {}).get("value") or {}).get("cursor", 0))
            previous = checkpoints.get("knowledge_reconciliation") or {}
            if previous.get("sealed") is True:
                value = previous.get("value") or {}
                return {"generation_id": generation_id, "status": "complete", "page_count": 0,
                        "change_count": value.get("change_count", 0), "cursor": value.get("cursor", 0)}
            value = previous.get("value") or {}
            cursor = int(value.get("cursor", 0))
            change_count = int(value.get("change_count", 0))
            page = (await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/changes/page",
                request=request, json={"after_change_id": cursor, "limit": page_size},
            )).get("changes", [])
            pending = next((i for i, change in enumerate(page) if not change.get("acknowledged", False)), None)
            ready_page = page if pending is None else page[:pending]
            for change in ready_page:
                if change["change_id"] > index_cursor:
                    raise RuntimeError("Index journal reconciliation must advance before Knowledge")
                source_id = change["source_id"]
                source = (await self.internal_client.request(
                    "POST", f"/api/v1/index/generations/{generation_id}/sources/query",
                    request=request, json={"source_ids": [source_id]},
                )).get("sources", {}).get(source_id)
                if source is None:
                    raise RuntimeError("reconciled Index source is missing from the target generation")
                if source.get("tombstoned"):
                    await self.internal_client.request(
                        "POST", f"/api/v1/knowledge/generations/{generation_id}/records",
                        request=request, json={"source_id": source_id, "tombstoned": True},
                    )
                    continue
                record = (await self.internal_client.request(
                    "POST", "/api/v1/knowledge/records/query",
                    request=request, json={"source_ids": [source_id]},
                )).get("records", {}).get(source_id)
                if record is None:
                    continue
                if record.get("tombstoned"):
                    await self.internal_client.request(
                        "POST", f"/api/v1/knowledge/generations/{generation_id}/records",
                        request=request, json={"source_id": source_id, "tombstoned": True},
                    )
                    continue
                if record["revision_sequence"] != source["high_sequence"]:
                    raise RuntimeError("active Knowledge revision does not match the reconciled Index source")
                await self.internal_client.request(
                    "POST", f"/api/v1/knowledge/generations/{generation_id}/records",
                    request=request, json={"source_id": source_id, "record": record},
                )
            next_cursor = ready_page[-1]["change_id"] if ready_page else cursor
            change_count += len(ready_page)
            high_watermark = (await self.internal_client.request(
                "GET", f"/api/v1/index/generations/{generation_id}/changes/high-watermark", request=request,
            )).get("high_watermark", 0)
            waiting = pending is not None
            caught_up = not waiting and next_cursor == high_watermark and len(page) < page_size
            checkpoint = {"cursor": next_cursor, "change_count": change_count,
                          "observed_high_watermark": high_watermark}
            await self.internal_client.request(
                "POST", f"/api/v1/index/generations/{generation_id}/checkpoints", request=request,
                json={"capability": "knowledge_reconciliation", "checkpoint": checkpoint, "sealed": False},
            )
            return {"generation_id": generation_id,
                    "status": "waiting_for_ack" if waiting else ("caught_up" if caught_up else "in_progress"),
                    "page_count": len(ready_page),
                    "pending_change_id": page[pending]["change_id"] if waiting else None,
                    "change_count": change_count, "cursor": next_cursor,
                    "high_watermark": high_watermark}

        status = await self.index_store.generation_status()
        generation = next((item for item in status["generations"] if item["generation_id"] == generation_id), None)
        if generation is None or generation["state"] != "building":
            raise RuntimeError("generation is not in building state")
        checkpoints = generation.get("checkpoints") or {}
        if (checkpoints.get("knowledge") or {}).get("sealed") is not True:
            raise RuntimeError("Knowledge source copy must be sealed before reconciliation")
        index_cursor = int(((checkpoints.get("index_reconciliation") or {}).get("value") or {}).get("cursor", 0))
        previous = checkpoints.get("knowledge_reconciliation") or {}
        if previous.get("sealed") is True:
            value = previous.get("value") or {}
            return {"generation_id": generation_id, "status": "complete",
                    "page_count": 0, "change_count": value.get("change_count", 0),
                    "cursor": value.get("cursor", 0)}
        value = previous.get("value") or {}
        cursor = int(value.get("cursor", 0))
        change_count = int(value.get("change_count", 0))
        page = await self.index_store.list_generation_changes_after(generation_id, cursor, page_size)
        pending_change = next((i for i, change in enumerate(page) if not change.get("acknowledged", False)), None)
        ready_page = page if pending_change is None else page[:pending_change]
        for change in ready_page:
            if change["change_id"] > index_cursor:
                raise RuntimeError("Index journal reconciliation must advance before Knowledge")
            source = await self.index_store.get_generation_source(generation_id, change["source_id"])
            if source is None:
                raise RuntimeError("reconciled Index source is missing from the target generation")
            if source["tombstoned"]:
                await self.knowledge_store.tombstone_generation_source(generation_id, change["source_id"])
                continue
            record = await self.knowledge_store.get(change["source_id"])
            if record is None:
                continue
            if record.get("tombstoned"):
                await self.knowledge_store.tombstone_generation_source(generation_id, change["source_id"])
                continue
            if record["revision_sequence"] != source["high_sequence"]:
                raise RuntimeError("active Knowledge revision does not match the reconciled Index source")
            await self.knowledge_store.upsert_generation(generation_id, record)
        next_cursor = ready_page[-1]["change_id"] if ready_page else cursor
        change_count += len(ready_page)
        high_watermark = await self.index_store.generation_change_high_watermark(generation_id)
        waiting = pending_change is not None
        caught_up = not waiting and next_cursor == high_watermark and len(page) < page_size
        checkpoint = {"cursor": next_cursor, "change_count": change_count,
                      "observed_high_watermark": high_watermark}
        await self.index_store.record_generation_checkpoint(
            generation_id, "knowledge_reconciliation", checkpoint, sealed=False,
        )
        return {"generation_id": generation_id,
                "status": "waiting_for_ack" if waiting else ("caught_up" if caught_up else "in_progress"),
                "page_count": len(ready_page),
                "pending_change_id": page[pending_change]["change_id"] if waiting else None,
                "change_count": change_count, "cursor": next_cursor,
                "high_watermark": high_watermark}

