# ChromaDB storage and migration

Production Ensemble uses ChromaDB for vector records and cosine/HNSW retrieval. SQLite remains the source of truth for Index revisions, tombstones, generation journals, and the rest of Ensemble's durable application state. PostgreSQL is not introduced.

## Storage layout

By default, ChromaDB data is stored beneath the persistence root:

- Default partition: `$ENSEMBLE_PERSISTENCE_ROOT/chromadb/default`
- Named partition: `$ENSEMBLE_PERSISTENCE_ROOT/chromadb/partitions/<sha256(partition-id)>`

If `ENSEMBLE_PERSISTENCE_ROOT` is not set, Ensemble derives the location from the SQLite database path. Set `ENSEMBLE_CHROMA_PATH` only when intentionally opening an existing ChromaDB directory directly. The default partition uses that directory; named partitions use its `partitions/` subdirectory.

Keep ChromaDB inside the persistence root so the existing persistence backup and restore scripts include it. If an external path is deliberately configured, it must be backed up separately.

## Import the existing POC ChromaDB store

The POC default source path is `~/.FlossWare/poc-claude-ensemble/data/chromadb/1.5.9`.

First inspect the source collections:

```sh
python scripts/import-poc-chroma.py
```

Then back up Ensemble's persistence root and stop the Ensemble service. Import without deleting or modifying the POC source:

```sh
sudo bash scripts/backup-persistence.sh
sudo systemctl stop ensemble
python scripts/import-poc-chroma.py --apply
sudo systemctl start ensemble
```

The importer preserves collection names, record IDs, embeddings, documents, metadata, model IDs, and dimensions. It upserts into the Ensemble ChromaDB store and is safe to rerun. It does not migrate generation collections because the POC vector store does not use Ensemble's generation lifecycle.

## Migrate existing Ensemble SQLite vector rows

This is for installations created before the production ChromaDB backend was introduced. The migration copies both base vectors and generation vectors from each SQLite partition database. It leaves the SQLite tables untouched.

Dry run:

```sh
python scripts/migrate-sqlite-vectors-to-chroma.py
```

After backing up and stopping Ensemble:

```sh
sudo bash scripts/backup-persistence.sh
sudo systemctl stop ensemble
python scripts/migrate-sqlite-vectors-to-chroma.py --apply
sudo systemctl start ensemble
```

The operation is idempotent and non-destructive. It reports the number of base and generation vectors found per partition. It does not delete stale ChromaDB records or remove the old SQLite tables.

## Operational requirements

- Stop Ensemble before either import/migration so a concurrent writer cannot race the copy.
- Back up the persistence root before migration.
- Keep the ChromaDB directory in the persistence root for backup/restore coverage.
- Preserve the existing SQLite Index database; ChromaDB is not a replacement for Index revision and generation coordination.
- ChromaDB collections retain the POC cosine distance configuration and embedding model/dimension identity.
- Validate retrieval recall and latency against the POC corpus before tuning HNSW parameters or declaring full vector parity.
