# Append-only ledger implementation

The shared contract is in [append-only-ledger-contract.md](append-only-ledger-contract.md). This document records the Ensemble adapter details.

## Storage

The ledger is a first-class SQLite resource, separate from Memory recall/search and separate from `ensemble_memory_entries`.

- `ensemble_ledger_state` holds the stable ledger ID and current head.
- `ensemble_ledger_entries` stores immutable envelopes keyed by one-based sequence, with unique event IDs and entry hashes.
- Migration: `0029_append_only_ledger`.
- The ledger uses the existing `SqliteMemoryStore` database engine. It introduces no second database or PostgreSQL dependency.
- Write transactions serialize sequence allocation and imports. The application store also serializes in-process writes. Imports are transactional, so a validation or conflict failure rolls back the entire batch.

The migration revision check and Alembic metadata include the ledger tables. The existing Memory endpoints and append-once behavior remain unchanged.

## REST

The application exposes:

- `POST /api/v1/ledger/entries`
- `GET /api/v1/ledger/entries`
- `GET /api/v1/ledger/entries/{event_id}`
- `GET /api/v1/ledger/status`
- `POST /api/v1/ledger/verify`
- `POST /api/v1/ledger/import`

GET pull bounds the source scan to `limit` rows, advances the cursor through filtered-out rows, and returns ascending sequence order. Set `through_sequence` to the observed head for a finite traversal. `replay` and `export` are read-only adapter operations over the same sequence interval.

Full verification begins at sequence 1. Partial verification requires a caller-retained checkpoint with `ledger_id`, `sequence`, and `entry_sha256`. A local predecessor row alone is not considered a trusted checkpoint.

## Cross-repository transfer test

The same `parity/ledger-contract.json` fixture is used by both adapters. The optional live HTTP transfer test is skipped unless both service URLs are configured.

Run against live services:

```sh
POC_LEDGER_URL=http://127.0.0.1:8767 \
ENSEMBLE_LEDGER_URL=http://127.0.0.1:8080 \
python parity/ledger_http_transfer.py
```

The runner captures each source head, transfers bounded pages in both directions, and verifies that source ledger identity, head sequence, and head hash are unchanged.
