from __future__ import annotations

from typing import Any


class InMemoryVectorStore:
    def __init__(self):
        self._collections: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def list_collections(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "model": item["model"], "dimensions": item["dimensions"], "count": len(item["records"])}
            for name, item in sorted(self._collections.items())
        ]


    async def record_key_upper_bound(self, name: str) -> str | None:
        records = self._collections.get(name, {}).get("records", {})
        return max(records) if records else None

    async def list_records_after(
        self, name: str, cursor: str | None,
        upper_bound: str | None, limit: int,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 10000:
            raise ValueError("limit must be between 1 and 10000")
        if upper_bound is None:
            return []
        records = self._collections.get(name, {}).get("records", {})
        keys = [
            key for key in sorted(records)
            if key <= upper_bound and (cursor is None or key > cursor)
        ][:limit]
        return [{
            "id": key, "embedding": records[key]["embedding"],
            "document": records[key]["document"],
            "metadata": records[key]["metadata"],
        } for key in keys]

    async def existing(self, name: str, ids: list[str]) -> list[str]:
        collection = self._collections.get(name, {"records": {}})
        return [record_id for record_id in ids if record_id in collection["records"]]

    async def upsert(self, name: str, model: str, dimensions: int, items: list[dict[str, Any]]) -> int:
        collection = self._collections.setdefault(name, {"model": model, "dimensions": dimensions, "records": {}})
        if collection["model"] != model or collection["dimensions"] != dimensions:
            raise RuntimeError("vector collection model or dimension mismatch")
        for item in items:
            collection["records"][item["id"]] = dict(item)
        return len(items)

    async def update_metadata(self, name: str, items: list[dict[str, Any]]) -> int:
        collection = self._collections.get(name, {"records": {}})
        updated = 0
        for item in items:
            record = collection["records"].get(item["id"])
            if record is not None:
                record["metadata"] = dict(item["metadata"])
                updated += 1
        return updated

    async def delete_superseded(self, name: str, url: str, keep_sha256: str) -> int:
        collection = self._collections.get(name, {"records": {}})
        stale = [
            record_id for record_id, item in collection["records"].items()
            if item["metadata"].get("url") == url
            and item["metadata"].get("content_sha256") != keep_sha256
        ]
        for record_id in stale:
            del collection["records"][record_id]
        return len(stale)

    async def query(self, name: str, embedding: list[float], top_k: int) -> list[dict[str, Any]]:
        collection = self._collections.get(name, {"records": {}})
        scored = []
        for record_id, item in collection["records"].items():
            score = sum(left * right for left, right in zip(item["embedding"], embedding))
            scored.append({
                "id": record_id, "document": item["document"],
                "score": float(score), "metadata": item["metadata"],
            })
        scored.sort(key=lambda item: (-item["score"], item["id"]))
        return scored[:top_k]


    async def upsert_generation(
        self, generation_id: str, name: str, model: str,
        dimensions: int, items: list[dict[str, Any]],
    ) -> int:
        if not generation_id or len(generation_id) > 64:
            raise ValueError("generation_id must contain 1-64 characters")
        collection = self._collections.get(name)
        if collection is None:
            collection = {"model": model, "dimensions": dimensions, "records": {}}
            self._collections[name] = collection
        if collection["model"] != model or collection["dimensions"] != dimensions:
            raise RuntimeError("vector collection model or dimension mismatch")
        generation = collection.setdefault("generations", {}).setdefault(generation_id, {})
        for item in items:
            generation[item["id"]] = dict(item)
        return len(items)

    async def query_generation(
        self, generation_id: str, name: str,
        embedding: list[float], top_k: int,
    ) -> list[dict[str, Any]]:
        collection = self._collections.get(name, {})
        generation = collection.get("generations", {}).get(generation_id, {})
        scored = []
        for record_id, item in generation.items():
            if len(item["embedding"]) != len(embedding):
                continue
            score = sum(left * right for left, right in zip(item["embedding"], embedding))
            scored.append({
                "id": record_id, "document": item["document"],
                "score": float(score), "metadata": item["metadata"],
            })
        scored.sort(key=lambda item: (-item["score"], item["id"]))
        return scored[:max(0, top_k)]

    async def list_records_for_source(self, name: str, source_id: str, limit: int = 10000) -> list[dict[str, Any]]:
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("source_id is required")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000")
        collection = self._collections.get(name, {})
        matches = [
            {"id": record_id, "embedding": item["embedding"],
             "document": item["document"], "metadata": item["metadata"]}
            for record_id, item in collection.items()
            if item.get("metadata", {}).get("source_id") == source_id
            or item.get("metadata", {}).get("url") == source_id
        ]
        matches.sort(key=lambda item: item["id"])
        if len(matches) > limit:
            raise RuntimeError("source has too many vector records for one reconciliation page")
        return matches

    async def delete_generation_source(self, generation_id: str, source_id: str) -> int:
        removed = 0
        for collection in self._collections.values():
            generation = collection.get("generations", {}).get(generation_id, {})
            stale = [
                record_id for record_id, item in generation.items()
                if item.get("metadata", {}).get("source_id") == source_id
                or item.get("metadata", {}).get("url") == source_id
            ]
            for record_id in stale:
                del generation[record_id]
            removed += len(stale)
        return removed

    async def tombstone_generation_source(self, generation_id: str, source_id: str) -> int:
        return await self.delete_generation_source(generation_id, source_id)

    async def delete_generation(self, generation_id: str) -> int:
        removed = 0
        for collection in self._collections.values():
            removed += len(collection.get("generations", {}).pop(generation_id, {}))
        return removed
