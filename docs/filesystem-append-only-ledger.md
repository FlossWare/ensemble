# Filesystem-backed append-only ledger

Status: implementation decision for Ensemble ledger storage.

## Decision

The append-only ledger is stored in filesystem-backed JSON Lines (JSONL) files. SQLite is not the ledger's source of truth.

This decision applies only to the append-only ledger. Ensemble continues to use its existing SQLite persistence for mutable/query-oriented stores such as Memory, Vector, Index, Knowledge, Graph, GA, Thompson, Learning, and operational state. Do not migrate those stores as part of ledger work.

The POC JSONL ledger is the behavioral and storage reference. Ensemble must implement the same language-neutral REST contract without exposing its storage implementation.

## Storage layout

Use a dedicated ledger directory beneath the configured Ensemble persistence root:

- `<persistence-root>/ledger/events.jsonl`: authoritative append-only records.
- `<persistence-root>/ledger/ledger-meta.json`: stable ledger identity and format metadata; not an event store.
- `<persistence-root>/ledger/events.jsonl.lock`: cross-process coordination lock.
- Optional derived indexes/checkpoints may be added for performance, but must be rebuildable from the authoritative log and must never determine event truth.

Keep the initial implementation to one active JSONL file. Add numbered immutable segments only when measured size or operational requirements justify rotation. Rotation must preserve a single logical ledger identity and contiguous logical sequence.

## Write and durability rules

- Serialize each complete envelope as canonical UTF-8 JSON followed by exactly one newline.
- Acquire an exclusive OS file lock before checking duplicate IDs, allocating the next local sequence, validating the previous hash, and appending.
- Write the complete record, flush userspace buffers, and call `fsync` before reporting success.
- Never rewrite, update, or delete an accepted event through normal ledger operations.
- Same event ID and same canonical content is an idempotent duplicate. Same event ID with different content is a conflict.
- Keep batch import retry-safe. The API may report a durable committed prefix after an I/O failure; retrying the same source batch must not duplicate committed entries.
- A malformed or unterminated trailing record fails closed. Do not silently concatenate onto it or automatically truncate it. Recovery must be an explicit, auditable operation.

## Read and concurrency rules

- Readers acquire a shared file lock while establishing a bounded read view. Writers use an exclusive lock.
- Pulls are ordered by local sequence and bounded by a captured head sequence when the caller requests a finite traversal.
- Pull, replay, export, lookup, and verification are non-destructive.
- Cross-ledger imports retain source ledger ID and source sequence as provenance while assigning destination-local sequence numbers.
- File locks coordinate cooperating processes on supported local filesystems. Do not claim equivalent safety on arbitrary network filesystems without testing their locking and durability semantics.

## Integrity

Use the same canonicalization, payload digest, entry digest, and SHA-256 hash-chain rules and fixtures as the POC implementation. Verification must detect malformed records, sequence gaps/reordering, duplicate IDs, changed payloads, changed envelopes, and broken predecessor links.

A checkpoint or index is an optimization, not an authority. Partial verification is fully chain-verified only when anchored to sequence 1 or a trusted checkpoint retained outside the ledger. Legacy records remain explicitly legacy-unverified.

Hash chaining detects accidental or unauthorized changes only relative to a trusted checkpoint. It does not prevent a privileged process from rewriting the file and recomputing the chain.

## Derived indexes and scale

Start without a mandatory database index. Measure actual pull and lookup performance first. If needed, introduce a disposable sidecar index keyed by event ID and sequence, with enough file/segment offset information to seek efficiently. It must be rebuildable and safely discardable.

Do not add SQLite solely to index the ledger. If a future workload demonstrates a compelling need for SQL queries, evaluate that separately without changing the JSONL source of truth.

## Acceptance

- Ensemble's ledger events are persisted only in the filesystem JSONL log.
- SQLite remains in place for all unrelated Ensemble stores.
- Restart and multi-process tests prove durable append, lock coordination, duplicate/conflict behavior, and fail-closed recovery.
- Contract fixtures are shared with POC.
- POC-to-Ensemble and Ensemble-to-POC live HTTP transfer tests pass and prove source ledgers remain unchanged.
