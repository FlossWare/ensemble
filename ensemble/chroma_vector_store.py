from __future__ import annotations

import asyncio
from bisect import bisect_right
import hashlib
import json
import math
import os

import numpy as np
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
        self._record_id_snapshots: dict[tuple[str, str, str], list[str]] = {}
        self._vector_cache: dict[tuple[str, str, str | None], dict[str, Any]] = {}
        self._lock = RLock()

    def _partition_path(self) -> Path:
        partition = current_partition_id()
        configured = os.getenv("ENSEMBLE_CHROMA_PATH", "").strip()
        if configured:
            root = Path(configured).expanduser().resolve()
            if partition == "default":
                return root
            suffix = hashlib.sha256(partition.encode("utf-8")).hexdigest()
            return root / "partitions" / suffix
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

    def _invalidate_record_snapshot(self, name: str) -> None:
        partition = current_partition_id()
        with self._lock:
            stale = [
                key for key in self._record_id_snapshots
                if key[0] == partition and key[1] == name
            ]
            for key in stale:
                self._record_id_snapshots.pop(key, None)

    def _invalidate_vector_cache(self, name: str, generation_id: str | None = None) -> None:
        key = (current_partition_id(), name, generation_id)
        with self._lock:
            self._vector_cache.pop(key, None)

    def _invalidate_generation_caches(self, generation_id: str, name: str | None = None) -> None:
        partition = current_partition_id()
        with self._lock:
            stale = [
                key for key in self._vector_cache
                if key[0] == partition and key[2] == generation_id
                and (name is None or key[1] == name)
            ]
            for key in stale:
                self._vector_cache.pop(key, None)

    def _load_vector_cache(self, collection) -> dict[str, Any]:
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        vectors: list[Any] = []
        for offset in range(0, collection.count(), 2000):
            page = collection.get(
                include=["embeddings", "documents", "metadatas"],
                limit=2000, offset=offset,
            )
            ids.extend(page["ids"])
            documents.extend(page["documents"] or [])
            metadatas.extend(self._metadata_from_chroma(item) for item in (page["metadatas"] or []))
            vectors.extend(page["embeddings"] if page["embeddings"] is not None else [])
        metadata = collection.metadata or {}
        dimensions = int(metadata.get("ensemble_dimensions", metadata.get("dimensions", 0)) or 0)
        if vectors:
            dimensions = len(vectors[0])
        capacity = max(1024, 2 * len(ids))
        matrix = np.zeros((capacity, dimensions), dtype=np.float32)
        if ids:
            matrix[:len(ids)] = np.asarray(vectors, dtype=np.float32)
        return {
            "ids": ids, "documents": documents, "metadatas": metadatas,
            "buf": matrix, "rows": len(ids),
            "index": {record_id: row for row, record_id in enumerate(ids)},
        }

    def _apply_vector_upsert(
        self, name: str, items: list[dict[str, Any]],
        generation_id: str | None = None,
    ) -> None:
        key = (current_partition_id(), name, generation_id)
        with self._lock:
            entry = self._vector_cache.get(key)
            if entry is None:
                return
            for item in items:
                record_id = str(item["id"])
                row = entry["index"].get(record_id)
                if row is None:
                    if entry["rows"] == entry["buf"].shape[0]:
                        grown = np.zeros(
                            (2 * entry["buf"].shape[0], entry["buf"].shape[1]),
                            dtype=np.float32,
                        )
                        grown[:entry["rows"]] = entry["buf"][:entry["rows"]]
                        entry["buf"] = grown
                    row = entry["rows"]
                    entry["rows"] += 1
                    entry["index"][record_id] = row
                    entry["ids"].append(record_id)
                    entry["documents"].append(str(item["document"]))
                    entry["metadatas"].append(dict(item["metadata"]))
                else:
                    entry["documents"][row] = str(item["document"])
                    entry["metadatas"][row] = dict(item["metadata"])
                entry["buf"][row] = np.asarray(item["embedding"], dtype=np.float32)

    def _update_cached_metadata(
        self, name: str, items: list[dict[str, Any]],
        generation_id: str | None = None,
    ) -> None:
        key = (current_partition_id(), name, generation_id)
        with self._lock:
            entry = self._vector_cache.get(key)
            if entry is None:
                return
            for item in items:
                row = entry["index"].get(str(item["id"]))
                if row is not None:
                    entry["metadatas"][row] = dict(item["metadata"])

    def _exact_query(
        self, name: str, embedding: list[float], top_k: int,
        generation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        collection = self._get_collection(
            name, generation_id=generation_id, create=False,
        )
        key = (current_partition_id(), name, generation_id)
        with self._lock:
            entry = self._vector_cache.get(key)
            if entry is None:
                entry = self._load_vector_cache(collection)
                self._vector_cache[key] = entry
            rows = entry["rows"]
            if rows == 0:
                return []
            query = np.asarray(embedding, dtype=np.float32)
            if query.shape != (entry["buf"].shape[1],) or not np.isfinite(query).all():
                raise ValueError("query embedding dimensions or values are invalid")
            scores = entry["buf"][:rows] @ query
            eligible = [
                index for index in range(rows)
                if not entry["metadatas"][index].get("_tombstoned")
            ]
            order = sorted(
                eligible,
                key=lambda index: (-float(scores[index]), entry["ids"][index]),
            )[:top_k]
            return [{
                "id": entry["ids"][index],
                "document": entry["documents"][index],
                "score": float(scores[index]),
                "metadata": dict(entry["metadatas"][index]),
            } for index in order]

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
        result = await asyncio.to_thread(run)
        self._invalidate_record_snapshot(name)
        self._apply_vector_upsert(name, items)
        return result

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
            result = await asyncio.to_thread(run)
            if result:
                self._update_cached_metadata(name, items)
            return result
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
            result = await asyncio.to_thread(run)
            if result:
                self._invalidate_record_snapshot(name)
            return result
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
                    metadata = collection.metadata or {}
                    self._invalidate_vector_cache(
                        metadata.get("ensemble_name", collection.name),
                        metadata.get("ensemble_generation_id"),
                    )
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
                    metadata = collection.metadata or {}
                    self._invalidate_vector_cache(
                        metadata.get("ensemble_name", collection.name),
                        metadata.get("ensemble_generation_id"),
                    )
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
                    logical_name = (collection.metadata or {}).get("ensemble_name", collection.name)
                    self._invalidate_record_snapshot(logical_name)
                    self._invalidate_vector_cache(logical_name)
                total += len(selected)
                if len(ids) > 500:
                    return 501
            return total
        return await asyncio.to_thread(run)

    async def query(self, name: str, embedding: list[float], top_k: int) -> list[dict[str, Any]]:
        if top_k < 1:
            return []
        try:
            return await asyncio.to_thread(
                self._exact_query, name, embedding, top_k,
            )
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
        result = await asyncio.to_thread(run)
        self._apply_vector_upsert(name, items, generation_id)
        return result

    async def query_generation(self, generation_id: str, name: str, embedding: list[float], top_k: int) -> list[dict[str, Any]]:
        if top_k < 1:
            return []
        try:
            return await asyncio.to_thread(
                self._exact_query, name, embedding, top_k, generation_id,
            )
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
        result = await asyncio.to_thread(run)
        if result:
            self._update_cached_metadata(name, items, generation_id)
        return result

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
                    self._invalidate_vector_cache(
                        metadata.get("ensemble_name", collection.name), generation_id,
                    )
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
                    metadata = collection.metadata or {}
                    self._invalidate_vector_cache(
                        metadata.get("ensemble_name", collection.name), generation_id,
                    )
            return changed
        return await asyncio.to_thread(run)

    async def restore_generation_source(self, generation_id: str, source_id: str) -> int:
        def run():
            changed = 0
            for collection in self._list_collections():
                if (collection.metadata or {}).get("ensemble_generation_id") != generation_id:
                    continue
                rows = self._records_from_get(collection.get(where=self._where_source(source_id), include=["metadatas"]))
                selected = [(row["id"], row["metadata"]) for row in rows if row["metadata"].get("_tombstoned")]
                if selected:
                    for _, metadata in selected:
                        metadata["_tombstoned"] = False
                    collection.update(ids=[record_id for record_id, _ in selected],
                                      metadatas=[self._metadata_to_chroma(metadata) for _, metadata in selected])
                    changed += len(selected)
                    metadata = collection.metadata or {}
                    self._invalidate_vector_cache(
                        metadata.get("ensemble_name", collection.name), generation_id,
                    )
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
        result = await asyncio.to_thread(run)
        self._invalidate_generation_caches(generation_id)
        return result

    async def record_key_upper_bound(self, name: str) -> str | None:
        def run():
            collection = self._get_collection(name, create=False)
            ids = sorted(collection.get(include=[])["ids"])
            if not ids:
                return None
            upper_bound = ids[-1]
            partition = current_partition_id()
            with self._lock:
                stale = [
                    key for key in self._record_id_snapshots
                    if key[0] == partition and key[1] == name
                ]
                for key in stale:
                    self._record_id_snapshots.pop(key, None)
                self._record_id_snapshots[(partition, name, upper_bound)] = ids
            return upper_bound
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
            partition = current_partition_id()
            key = (partition, name, upper_bound)
            with self._lock:
                ids = self._record_id_snapshots.get(key)
            if ids is None:
                ids = sorted(
                    record_id for record_id in collection.get(include=[])["ids"]
                    if record_id <= upper_bound
                )
                with self._lock:
                    self._record_id_snapshots[key] = ids
            start = bisect_right(ids, cursor) if cursor is not None else 0
            selected = ids[start:start + limit]
            if not selected:
                return []
            result = collection.get(
                ids=selected, include=["embeddings", "documents", "metadatas"],
            )
            by_id = {row["id"]: row for row in self._records_from_get(result)}
            return [by_id[record_id] for record_id in selected if record_id in by_id]
        try:
            return await asyncio.to_thread(run)
        except Exception as exc:
            if self._is_missing_collection(exc):
                return []
            raise
