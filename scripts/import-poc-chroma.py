#!/usr/bin/env python3
"""Import POC PersistentClient collections into Ensemble's ChromaDB store.

Default source path matches the POC vector service. The script is dry-run by
default and only upserts into Ensemble when --apply is supplied. It never
deletes or mutates the source POC database.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

import chromadb

from ensemble.chroma_vector_store import ChromaVectorStore


def _collections(client: Any) -> list[Any]:
    listed = client.list_collections()
    return [
        item if hasattr(item, "metadata") else client.get_collection(name=item)
        for item in listed
    ]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=os.path.expanduser(
            "~/.FlossWare/poc-claude-ensemble/data/chromadb/1.5.9"
        ),
        help="POC ChromaDB persistence directory",
    )
    parser.add_argument(
        "--persistence-root",
        default=os.getenv("ENSEMBLE_PERSISTENCE_ROOT", ".ensemble"),
        help="Ensemble persistence root",
    )
    parser.add_argument("--collection", action="append", help="limit import to a collection; repeatable")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--apply", action="store_true", help="perform the import; otherwise show a dry run")
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 1000:
        raise SystemExit("--batch-size must be between 1 and 1000")

    source_path = Path(args.source).expanduser().resolve()
    if not source_path.is_dir():
        raise SystemExit(f"POC ChromaDB directory does not exist: {source_path}")
    source = chromadb.PersistentClient(path=str(source_path))
    target = ChromaVectorStore(persistence_root=args.persistence_root)
    selected = _collections(source)
    if args.collection:
        wanted = set(args.collection)
        selected = [item for item in selected if item.name in wanted]
        missing = wanted - {item.name for item in selected}
        if missing:
            raise SystemExit(f"POC collections not found: {', '.join(sorted(missing))}")

    total = 0
    for collection in selected:
        metadata = collection.metadata or {}
        model = metadata.get("ensemble_model", metadata.get("model"))
        dimensions = metadata.get("ensemble_dimensions", metadata.get("dimensions"))
        if not isinstance(model, str) or not model:
            raise SystemExit(f"Collection {collection.name} has no embedding model metadata.")
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 1:
            raise SystemExit(f"Collection {collection.name} has invalid dimensions metadata.")
        count = collection.count()
        total += count
        print(
            f"{collection.name}: {count} vectors, model={model}, "
            f"dimensions={dimensions}, space={metadata.get('hnsw:space', 'unspecified')}"
        )
        if not args.apply:
            continue
        for offset in range(0, count, args.batch_size):
            result = collection.get(
                limit=min(args.batch_size, count - offset),
                offset=offset,
                include=["embeddings", "documents", "metadatas"],
            )
            embeddings = result.get("embeddings")
            documents = result.get("documents")
            metadatas = result.get("metadatas")
            if embeddings is None or documents is None or metadatas is None:
                raise RuntimeError(f"Collection {collection.name} returned incomplete records.")
            items = [{
                "id": str(result["ids"][index]),
                "embedding": embeddings[index].tolist() if hasattr(embeddings[index], "tolist") else list(embeddings[index]),
                "document": str(documents[index]),
                "metadata": metadatas[index] or {},
            } for index in range(len(result["ids"]))]
            await target.upsert(collection.name, model, dimensions, items)

    if args.apply:
        await target.close()
    print(f"{'IMPORTED' if args.apply else 'DRY RUN'}: {total} vectors across {len(selected)} collections")
    if not args.apply:
        print("Review the collection inventory, then rerun with --apply.")


if __name__ == "__main__":
    asyncio.run(main())
