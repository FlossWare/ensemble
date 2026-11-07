from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
from threading import RLock
from typing import Any

from sqlalchemy.engine import make_url

from .partitioning import current_partition_id


class ChromaVectorStore:
    """ChromaDB persistent vector backend with partition-isolated storage.

    SQLite remains the authority for Index, source revisions, and generation
    coordination. Vector records and similarity search live in ChromaDB.
    """

    def __init__(
        self, persistence_root: str | Path | None = None,
        database_url: str | None = None,
    ):
        configured = persistence_root or os.getenv("ENSEMBLE_PERSISTENCE_ROOT")
        if configured:
            root = Path(configured).expanduser()
        elif database_url:
            parsed = make_url(database_url)
            database = Path(parsed.database or ".ensemble/ensemble.db").expanduser().resolve()
            root = database.parent / (
                "chromadb" if database.name == "ensemble.db"
                else f"{database.stem}-chromadb"
            )
        else:
            root = Path(".ensemble")
        self.persistence_root = root.resolve()
        self._clients: dict[str, Any] = {}
        self._lock = RLock()

    def _partition_path(self) -> Path:
        partition = current_partition_id()
        if partition == "default":
            suffix = "default"
        else:
            suffix = hashlib.sha256(partition.encode("utf-8")).hexdigest()
        return self.persistence_root / "chromadb" / suffix

    def _client(self):
        import chromadb

        partition = current_partition_id()
        with self._lock:
            client = self._clients.get(partition)
            if client is None:
                path = self._partition_path()
                path.mkdir(parents=True, exist_ok=True)
                client = chromadb.PersistentClient(path=str(path))
                self._clients[partition] = client
            return client

    def _list_collections(self):
        client = self._client()
        listed = client.list_collections()
        # ChromaDB <=0.5 returned Collection objects; newer releases return names.
        return [
            item if hasattr(item, "metadata") else client.get_collection(name=item)
            for item in listed
        ]

    @staticmethod
    def _generation_name(generation_id: str, name: str) -> str:
        digest = hashlib.sha256(f"{generation_id}\0{name}".encode("utf-8")).hexdigest()[:48]
        return f"ens-g-{digest}"

    @staticmethod
    def _collection_name(name: str) -> str:
        if len(name) <= 63 and len(name) >= 3:
            return name
        return "ens-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:48]

    @staticmethod
    def _metadata_to_chroma(metadata: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        result: dict[str, Any] = {}
        for key, value in metadata.items():
            if isinstance(value, (str, int, bool)):
                result[key] = value
            elif isinstance(value, float) and math.isfinite(value):
                result[key] = value
            else:
                result[f"{key}__ensemble_json"] = json.dumps(
                    value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    allow_nan=False,
                )
        if not result:
            result["_ensemble_record"] = True
        return result

    @staticmethod
    def _metadata_from_chroma(metadata: dict[str, Any] | None) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in (metadata or {}).items():
            if key.endswith("__ensemble_json"):
                original = key[:-len("__ensemble_json")]
                try:
                    result[original] = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    result[original] = value
            elif key != "_ensemble_record":
                result[key] = value
        return result

    def _get_collection(
        self, name: str, model: str | None = None, dimensions: int | None = None,
        generation_id: str | None = None, create: bool = True,
    ):
        physical = self._generation_name(generation_id, name) if generation_id else self._collection_name(name)
        client = self._client()
        if create:
            metadata = {
                "hnsw:space": "cosine",
                "ensemble_name": name,
                "ensemble_model": model or "unknown",
                "ensemble_dimensions": int(dimensions or 0),
            }
            if generation_id:
                metadata["ensemble_generation_id"] = generation_id
            collection = client.get_or_create_collection(
                name=physical, metadata=metadata,
            )
        else:
            collection = client.get_collection(name=physical)
        stored = collection.metadata or {}
        if stored.get("ensemble_name") not in (None, name):
            raise RuntimeError("ChromaDB collection identity mismatch")
        stored_model = stored.get("ensemble_model", stored.get("model"))
        stored_dimensions = stored.get("ensemble_dimensions", stored.get("dimensions"))
        if model is not None and stored_model not in (None, "unknown", model):
            raise RuntimeError(
                f"collection {name} uses model={stored_model}; request uses model={model}"
            )
        if dimensions is not None and stored_dimensions not in (None, 0, dimensions):
            raise RuntimeError(
                f"collection {name} uses dimensions={stored_dimensions}; request uses dimensions={dimensions}"
            )
        if stored.get("hnsw:space") not in (None, "cosine"):
            raise RuntimeError(f"collection {name} does not use cosine distance")
        if stored.get("ensemble_model") in (None, "unknown") and model is not None:
            collection.modify(metadata={
                **stored, "hnsw:space": "cosine", "ensemble_name": name,
                "ensemble_model": model, "ensemble_dimensions": int(dimensions or 0),
                **({"ensemble_generation_id": generation_id} if generation_id else {}),
            })
        return collection

    @staticmethod
    def _is_missing_collection(exc: Exception) -> bool:
        message = str(exc).casefold()
        return "not found" in message or "does not exist" in message

    @staticmethod
    def _where_source(source_id: str) -> dict[str, Any]:
        return {"$or": [{"source_id": source_id}, {"url": source_id}]}

    @classmethod
    def _records_from_get(cls, result: dict[str, Any]) -> list[dict[str, Any]]:
        ids = result.get("ids") or []
        documents = result.get("documents") or [None] * len(ids)
        metadatas = result.get("metadatas") or [{}] * len(ids)
        embeddings = result.get("embeddings")
        rows = []
        for index, record_id in enumerate(ids):
            row = {
                "id": record_id,
                "document": documents[index],
                "metadata": cls._metadata_from_chroma(metadatas[index]),
            }
            if embeddings is not None:
                row["embedding"] = embeddings[index].tolist() if hasattr(embeddings[index], "tolist") else list(embeddings[index])
            rows.append(row)
        return rows

    async def initialize(self) -> None:
        await asyncio.to_thread(self._client)

    async def close(self) -> None:
        # PersistentClient has no public close API. Keep its process-level client
        # cached so another app lifespan cannot open the same path twice.
        return None

    async def list_collections(self) -> list[dict[str, Any]]:
        def run():
            result = []
            for collection in self._list_collections():
                metadata = collection.metadata or {}
                if metadata.get("ensemble_generation_id"):
                    continue
                result.append({
                    "name": metadata.get("ensemble_name", collection.name),
                    "model": metadata.get("ensemble_model", metadata.get("model", "unknown")),
                    "dimensions": metadata.get("ensemble_dimensions", metadata.get("dimensions", 0)),
                    "count": collection.count(),
                })
            return sorted(result, key=lambda item: item["name"])
        return await asyncio.to_thread(run)

    async def existing(self, name: str, ids: list[str]) -> list[str]:
        if not ids:
            return []
        def run():
            collection = self._get_collection(name)
            found = set(collection.get(ids=ids, include=[])["ids"])
            return [record_id for record_id in ids if record_id in found]
        return await asyncio.to_thread(run)

    async def upsert(self, name: str, model: str, dimensions: int, items: list[dict[str, Any]]) -> int:
        if not items:
            return 0
        if dimensions < 1:
            raise ValueError("dimensions must be positive")
        def run():
            collection = self._get_collection(name, model, dimensions)
            for offset in range(0, len(items), 256):
                batch = items[offset:offset + 256]
                for item in batch:
                    vector = item["embedding"]
                    if len(vector) != dimensions:
                        raise ValueError("embedding dimensions do not match collection")
                    if not all(math.isfinite(float(value)) for value in vector):
                        raise ValueError("embeddings must contain only finite values")
                collection.upsert(
                    ids=[str(item["id"]) for item in batch],
                    embeddings=[[float(value) for value in item["embedding"]] for item in batch],
                    documents=[str(item["document"]) for item in batch],
                    metadatas=[self._metadata_to_chroma(item["metadata"]) for item in batch],
                )
            return len(items)
        return await asyncio.to_thread(run)

    async def update_metadata(self, name: str, items: list[dict[str, Any]]) -> int:
        if not items:
            return 0
        def run():
            collection = self._get_collection(name, create=False)
            ids = [str(item["id"]) for item in items]
            present = set(collection.get(ids=ids, include=[])["ids"])
            selected = [item for item in items if str(item["id"]) in present]
            if selected:
                collection.update(
                    ids=[str(item["id"]) for item in selected],
                    metadatas=[self._metadata_to_chroma(item["metadata"]) for item in selected],
                )
            return len(selected)
        try:
            return await asyncio.to_thread(run)
        except Exception as exc:
            if self._is_missing_collection(exc):
                return 0
            raise

    async def delete_superseded(self, name: str, url: str, keep_sha256: str) -> int:
        def run():
            collection = self._get_collection(name, create=False)
            found = collection.get(
                where={"$and": [{"url": url}, {"content_sha256": {"$ne": keep_sha256}}]},
                include=[],
            )["ids"]
            if found:
                collection.delete(ids=found)
            return len(found)
        try:
            return await asyncio.to_thread(run)
        except Exception as exc:
            if self._is_missing_collection(exc):
                return 0
            raise

    async def tombstone_source(self, source_id: str) -> int:
        def run():
            changed = 0
            for collection in self._list_collections():
                rows = self._records_from_get(collection.get(where=self._where_source(source_id), include=["metadatas"]))
                selected = [(row["id"], row["metadata"]) for row in rows if not row["metadata"].get("_tombstoned")]
                if selected:
                    for _, metadata in selected:
                        metadata["_tombstoned"] = True
                    collection.update(
                        ids=[record_id for record_id, _ in selected],
                        metadatas=[self._metadata_to_chroma(metadata) for _, metadata in selected],
                    )
                    changed += len(selected)
            return changed
        return await asyncio.to_thread(run)

    async def restore_source(self, source_id: str) -> int:
        def run():
            changed = 0
            for collection in self._list_collections():
                rows = self._records_from_get(collection.get(where=self._where_source(source_id), include=["metadatas"]))
                selected = [(row["id"], row["metadata"]) for row in rows if row["metadata"].get("_tombstoned")]
                if selected:
                    for _, metadata in selected:
                        metadata["_tombstoned"] = False
                    collection.update(
                        ids=[record_id for record_id, _ in selected],
                        metadatas=[self._metadata_to_chroma(metadata) for _, metadata in selected],
                    )
                    changed += len(selected)
            return changed
        return await asyncio.to_thread(run)

    async def reclaim_source(self, source_id: str) -> int:
        def run():
            total = 0
            for collection in self._list_collections():
                if (collection.metadata or {}).get("ensemble_generation_id"):
                    continue
                ids = collection.get(where=self._where_source(source_id), include=[])["ids"]
                selected = ids[:500]
                if selected:
                    collection.delete(ids=selected)
                total += len(selected)
                if len(ids) > 500:
                    return 501
            return total
        return await asyncio.to_thread(run)

    async def query(self, name: str, embedding: list[float], top_k: int) -> list[dict[str, Any]]:
        if top_k < 1:
            return []
        def run():
            collection = self._get_collection(name, create=False)
            count = collection.count()
            requested = min(count, max(top_k, 32))
            rows = []
            while requested:
                result = collection.query(
                    query_embeddings=[[float(value) for value in embedding]],
                    n_results=requested,
                    include=["documents", "metadatas", "distances"],
                )
                ids = (result.get("ids") or [[]])[0]
                docs = (result.get("documents") or [[]])[0]
                metas = (result.get("metadatas") or [[]])[0]
                distances = (result.get("distances") or [[]])[0]
                rows = [{
                    "id": record_id, "document": docs[index],
                    "score": float(1.0 - distances[index]),
                    "metadata": self._metadata_from_chroma(metas[index]),
                } for index, record_id in enumerate(ids)
                    if not self._metadata_from_chroma(metas[index]).get("_tombstoned")]
                if len(rows) >= top_k or requested >= count:
                    break
                requested = min(count, requested * 2)
            return sorted(rows, key=lambda item: (-item["score"], item["id"]))[:top_k]
        try:
            return await asyncio.to_thread(run)
        except Exception as exc:
            if self._is_missing_collection(exc):
                return []
            raise

    async def upsert_generation(self, generation_id: str, name: str, model: str, dimensions: int, items: list[dict[str, Any]]) -> int:
        if not generation_id or len(generation_id) > 64:
            raise ValueError("generation_id must contain 1-64 characters")
        if not items:
            return 0
        def run():
            collection = self._get_collection(name, model, dimensions, generation_id)
            for item in items:
                vector = item["embedding"]
                if len(vector) != dimensions:
                    raise ValueError("embedding dimensions do not match collection")
                if not all(math.isfinite(float(value)) for value in vector):
                    raise ValueError("embeddings must contain only finite values")
            collection.upsert(
                ids=[str(item["id"]) for item in items],
                embeddings=[[float(value) for value in item["embedding"]] for item in items],
                documents=[str(item["document"]) for item in items],
                metadatas=[self._metadata_to_chroma(item["metadata"]) for item in items],
            )
            return len(items)
        return await asyncio.to_thread(run)

    async def query_generation(self, generation_id: str, name: str, embedding: list[float], top_k: int) -> list[dict[str, Any]]:
        if top_k < 1:
            return []
        def run():
            collection = self._get_collection(name, generation_id=generation_id, create=False)
            count = collection.count()
            requested = min(count, max(top_k, 32))
            rows = []
            while requested:
                result = collection.query(
                    query_embeddings=[[float(value) for value in embedding]],
                    n_results=requested,
                    include=["documents", "metadatas", "distances"],
                )
                ids = (result.get("ids") or [[]])[0]
                docs = (result.get("documents") or [[]])[0]
                metas = (result.get("metadatas") or [[]])[0]
                distances = (result.get("distances") or [[]])[0]
                rows = [{
                    "id": record_id, "document": docs[index],
                    "score": float(1.0 - distances[index]),
                    "metadata": self._metadata_from_chroma(metas[index]),
                } for index, record_id in enumerate(ids)
                    if not self._metadata_from_chroma(metas[index]).get("_tombstoned")]
                if len(rows) >= top_k or requested >= count:
                    break
                requested = min(count, requested * 2)
            return sorted(rows, key=lambda item: (-item["score"], item["id"]))[:top_k]
        try:
            return await asyncio.to_thread(run)
        except Exception as exc:
            if self._is_missing_collection(exc):
                return []
            raise

    async def list_records_for_source(self, name: str, source_id: str, limit: int = 10000) -> list[dict[str, Any]]:
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("source_id is required")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000")
        def run():
            collection = self._get_collection(name, create=False)
            result = collection.get(
                where=self._where_source(source_id), include=["embeddings", "documents", "metadatas"],
                limit=limit + 1,
            )
            rows = self._records_from_get(result)
            if len(rows) > limit:
                raise RuntimeError("source has too many vector records for one reconciliation page")
            return rows
        try:
            return await asyncio.to_thread(run)
        except Exception as exc:
            if self._is_missing_collection(exc):
                return []
            raise

    async def update_generation_metadata(self, generation_id: str, name: str, items: list[dict[str, Any]]) -> int:
        if not items:
            return 0
        def run():
            collection = self._get_collection(name, generation_id=generation_id, create=False)
            ids = [str(item["id"]) for item in items]
            present = set(collection.get(ids=ids, include=[])["ids"])
            selected = [item for item in items if str(item["id"]) in present]
            if selected:
                collection.update(
                    ids=[str(item["id"]) for item in selected],
                    metadatas=[self._metadata_to_chroma(item["metadata"]) for item in selected],
                )
            return len(selected)
        return await asyncio.to_thread(run)

    async def delete_generation_source(self, generation_id: str, source_id: str, name: str | None = None) -> int:
        def run():
            removed = 0
            for collection in self._list_collections():
                metadata = collection.metadata or {}
                if metadata.get("ensemble_generation_id") != generation_id:
                    continue
                if name is not None and metadata.get("ensemble_name") != name:
                    continue
                ids = collection.get(where=self._where_source(source_id), include=[])["ids"]
                selected = ids[:500]
                if selected:
                    collection.delete(ids=selected)
                removed += len(selected)
                if len(ids) > 500:
                    return 501
            return removed
        return await asyncio.to_thread(run)

    async def tombstone_generation_source(self, generation_id: str, source_id: str) -> int:
        def run():
            changed = 0
            for collection in self._list_collections():
                if (collection.metadata or {}).get("ensemble_generation_id") != generation_id:
                    continue
                rows = self._records_from_get(collection.get(where=self._where_source(source_id), include=["metadatas"]))
                selected = [(row["id"], row["metadata"]) for row in rows if not row["metadata"].get("_tombstoned")]
                if selected:
                    for _, metadata in selected:
                        metadata["_tombstoned"] = True
                    collection.update(ids=[record_id for record_id, _ in selected],
                                      metadatas=[self._metadata_to_chroma(metadata) for _, metadata in selected])
                    changed += len(selected)
            return changed
        return await asyncio.to_thread(run)

    async def restore_generation_source(self, generation_id: str, source_id: str) -> int:
        def run():
            changed = 0
            for collection in self._list_collections():
                if (collection.metadata or {}).get("ensemble_generation_id") != generation_id:
                    continue
                rows = self._records_from_get(collection.get(where=self._where_source(source_id), include=["metadatas"]))
                selected = [(row["id"], row["metadata"]) for row in rows if row["metadata"].pop("_tombstoned", None)]
                if selected:
                    collection.update(ids=[record_id for record_id, _ in selected],
                                      metadatas=[self._metadata_to_chroma(metadata) for _, metadata in selected])
                    changed += len(selected)
            return changed
        return await asyncio.to_thread(run)

    async def delete_generation(self, generation_id: str) -> int:
        def run():
            client = self._client()
            targets = [collection for collection in self._list_collections()
                       if (collection.metadata or {}).get("ensemble_generation_id") == generation_id]
            removed = 0
            for collection in targets:
                removed += collection.count()
                client.delete_collection(collection.name)
            return removed
        return await asyncio.to_thread(run)

    async def record_key_upper_bound(self, name: str) -> str | None:
        def run():
            collection = self._get_collection(name, create=False)
            ids = collection.get(include=[])["ids"]
            return max(ids) if ids else None
        try:
            return await asyncio.to_thread(run)
        except Exception as exc:
            if self._is_missing_collection(exc):
                return None
            raise

    async def list_records_after(self, name: str, cursor: str | None, upper_bound: str | None, limit: int) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000")
        if upper_bound is None:
            return []
        def run():
            collection = self._get_collection(name, create=False)
            result = collection.get(include=["embeddings", "documents", "metadatas"])
            rows = self._records_from_get(result)
            rows = [row for row in rows if row["id"] <= upper_bound and (cursor is None or row["id"] > cursor)]
            rows.sort(key=lambda row: row["id"])
            return rows[:limit]
        try:
            return await asyncio.to_thread(run)
        except Exception as exc:
            if self._is_missing_collection(exc):
                return []
            raise
