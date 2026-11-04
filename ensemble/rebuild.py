"""Resumable generation-building operations.

The builder deliberately performs one bounded page per call. A caller can
schedule repeated calls, inspect the durable checkpoint, and resume after a
process restart without holding a long-lived transaction.
"""

from __future__ import annotations

from typing import Any


class IndexGenerationBuilder:
    def __init__(self, index_store: Any):
        self.index_store = index_store

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
