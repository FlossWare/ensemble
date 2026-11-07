#!/usr/bin/env python3
"""Copy legacy SQLite vector records into the production ChromaDB backend.

The operation is idempotent and non-destructive. It never deletes SQLite rows.
Run with the Ensemble service stopped, inspect the dry-run summary, then pass
--apply to copy records. Partition databases are migrated into their matching
partition-specific ChromaDB directories.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from ensemble.chroma_vector_store import ChromaVectorStore
from ensemble.partitioning import RequestScope, reset_request_scope, set_request_scope


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _database_path(database_url: str) -> Path:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "sqlite":
        raise SystemExit("Only SQLite source databases can be migrated.")
    if parsed.database in (None, "", ":memory:"):
        raise SystemExit("A durable SQLite database path is required.")
    return Path(parsed.database).expanduser().resolve()


def _partition_id(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "ensemble_partition_metadata" not in tables:
            raise RuntimeError(f"partition marker is missing from {path}")
        row = connection.execute(
            "SELECT partition_id FROM ensemble_partition_metadata LIMIT 1"
        ).fetchone()
        if row is None or not row[0]:
            raise RuntimeError(f"partition marker is empty in {path}")
        return str(row[0])
    finally:
        connection.close()


def _read_collections(connection: sqlite3.Connection) -> dict[str, tuple[str, int]]:
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if "ensemble_vector_collections" not in tables:
        return {}
    rows = connection.execute(
        "SELECT name, model, dimensions FROM ensemble_vector_collections ORDER BY name"
    ).fetchall()
    return {str(name): (str(model), int(dimensions)) for name, model, dimensions in rows}


async def _migrate_partition(
    store: ChromaVectorStore, path: Path, partition_id: str,
    batch_size: int, apply: bool, include_generations: bool,
) -> tuple[int, int]:
    scope_token = set_request_scope(RequestScope(partition_id=partition_id))
    try:
        if apply:
            await store.initialize()
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            collections = _read_collections(connection)
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            base_total = 0
            generation_total = 0
            for name, (model, dimensions) in collections.items():
                if "ensemble_vector_records" not in tables:
                    break
                total = int(connection.execute(
                    "SELECT COUNT(*) FROM ensemble_vector_records WHERE collection_name=?",
                    (name,),
                ).fetchone()[0])
                base_total += total
                for offset in range(0, total, batch_size):
                    rows = connection.execute(
                        """SELECT record_id, embedding, document, metadata
                           FROM ensemble_vector_records
                           WHERE collection_name=?
                           ORDER BY record_id LIMIT ? OFFSET ?""",
                        (name, batch_size, offset),
                    ).fetchall()
                    items = [{
                        "id": str(row["record_id"]),
                        "embedding": _json(row["embedding"]),
                        "document": str(row["document"]),
                        "metadata": _json(row["metadata"]) or {},
                    } for row in rows]
                    if apply and items:
                        await store.upsert(name, model, dimensions, items)

            if include_generations and "ensemble_vector_generation_records" in tables:
                pairs = connection.execute(
                    """SELECT DISTINCT generation_id, collection_name
                       FROM ensemble_vector_generation_records
                       ORDER BY generation_id, collection_name"""
                ).fetchall()
                for generation_id, name in pairs:
                    if name not in collections:
                        raise RuntimeError(
                            f"generation {generation_id} references missing collection {name}"
                        )
                    model, dimensions = collections[name]
                    total = int(connection.execute(
                        """SELECT COUNT(*) FROM ensemble_vector_generation_records
                           WHERE generation_id=? AND collection_name=?""",
                        (generation_id, name),
                    ).fetchone()[0])
                    generation_total += total
                    for offset in range(0, total, batch_size):
                        rows = connection.execute(
                            """SELECT record_id, embedding, document, metadata
                               FROM ensemble_vector_generation_records
                               WHERE generation_id=? AND collection_name=?
                               ORDER BY record_id LIMIT ? OFFSET ?""",
                            (generation_id, name, batch_size, offset),
                        ).fetchall()
                        items = [{
                            "id": str(row["record_id"]),
                            "embedding": _json(row["embedding"]),
                            "document": str(row["document"]),
                            "metadata": _json(row["metadata"]) or {},
                        } for row in rows]
                        if apply and items:
                            await store.upsert_generation(
                                str(generation_id), str(name), model, dimensions, items,
                            )
            return base_total, generation_total
        finally:
            connection.close()
    finally:
        reset_request_scope(scope_token)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.getenv("ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db"),
    )
    parser.add_argument("--persistence-root", default=os.getenv("ENSEMBLE_PERSISTENCE_ROOT"))
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--no-generations", action="store_true")
    parser.add_argument("--apply", action="store_true", help="perform the copy; otherwise show a dry run")
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 1000:
        raise SystemExit("--batch-size must be between 1 and 1000")

    database = _database_path(args.database_url)
    root = Path(args.persistence_root).expanduser().resolve() if args.persistence_root else database.parent
    candidates = [database, *sorted((root / "partitions").glob("*.db"))]
    databases = [path for path in candidates if path.is_file()]
    if not databases:
        raise SystemExit("No SQLite persistence databases found.")
    store = ChromaVectorStore(
        persistence_root=args.persistence_root,
        database_url=args.database_url,
    )
    total_base = 0
    total_generations = 0
    for path in databases:
        partition = _partition_id(path)
        base_count, generation_count = await _migrate_partition(
            store, path, partition, args.batch_size, args.apply,
            not args.no_generations,
        )
        total_base += base_count
        total_generations += generation_count
        print(
            f"{partition}: {base_count} base vectors, "
            f"{generation_count} generation vectors"
        )
    mode = "COPIED" if args.apply else "DRY RUN"
    print(f"{mode}: {total_base} base vectors, {total_generations} generation vectors")
    if not args.apply:
        print("Stop Ensemble, review this summary, then rerun with --apply.")


if __name__ == "__main__":
    asyncio.run(main())
