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
