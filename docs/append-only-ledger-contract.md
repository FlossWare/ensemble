# Append-only Ledger REST Contract

Status: proposed implementation contract for POC and Ensemble parity.

## 1. Purpose and invariants

The ledger is an ordered, append-only record of events. It is not a search index and is not a consumptive queue.

- Appending creates a new record; normal ledger operations never update or delete an existing record.
- Pulling, reading, replaying, exporting, and verifying are non-destructive.
- Each ledger has a stable identity and a monotonically increasing, one-based sequence.
- Event IDs are stable within a ledger. Repeating the same ID and canonical payload is an idempotent duplicate; reusing an ID with different content is a conflict.
- A pull cursor identifies the last sequence delivered, not an offset into a filtered result set.
- The POC's existing JSONL ledger remains intact and is the behavioral reference. Ensemble implements the same public contract over its own persistence adapter.

## 2. Canonical record envelope

New records use this logical envelope:

| Field | Meaning |
|---|---|
| schema_version | Envelope schema version |
| ledger_id | Stable logical ledger identifier |
| sequence | One-based append order, unique and contiguous within the ledger |
| event_id | Stable event identifier |
| event_type | Event kind/type |
| source | Producer or source identity |
| timestamp | Event time supplied by producer, when available |
| captured_at | UTC time accepted by the ledger |
| correlation_id | Optional request/workflow correlation |
| payload | Producer-defined JSON object |
| payload_sha256 | SHA-256 of canonical payload JSON |
| previous_entry_sha256 | Hash of the previous envelope, or null for sequence 1 |
| entry_sha256 | SHA-256 of the canonical envelope excluding entry_sha256 |

Canonical JSON uses UTF-8, sorted object keys, compact separators, and no NaN/Infinity values. Hash comparisons are constant-time where practical. The exact canonicalization algorithm and hash input fields must be shared by both implementations and covered by fixtures.

Legacy POC records without sequence/hash-chain fields remain readable. Their sequence is their one-based physical append order. Ledger pulls expose these rows as schema-version-0 compatibility envelopes with the original row preserved in provenance, a deterministic synthetic event ID only when the source row has none, and null entry/hash-chain fields. A computed payload digest on this compatibility envelope is not evidence that the original legacy row was tamper-evident. Verification must report the legacy assurance level rather than claiming tamper-evident verification.

## 3. REST operations

The public REST paths are versioned and language-neutral. Implementations may retain compatibility aliases for existing endpoints.

| Method | Path | Purpose |
|---|---|---|
| POST | /api/v1/ledger/entries | Append one event |
| GET | /api/v1/ledger/entries | Bounded ordered pull with filters and cursor |
| GET | /api/v1/ledger/entries/{event_id} | Read one event |
| GET | /api/v1/ledger/status | Ledger identity, head sequence/hash, and counts |
| POST | /api/v1/ledger/verify | Verify a bounded sequence range or the full ledger |
| POST | /api/v1/ledger/import | Idempotently import an ordered batch from another ledger |

No ordinary API exposes update or delete operations for ledger records.

## 4. Pull and cursor semantics

GET /api/v1/ledger/entries accepts:

- after_sequence (default 0): return records with sequence greater than this value.
- through_sequence (optional): inclusive upper sequence bound.
- limit (bounded; default 100, maximum 1000).
- event_id, event_type, source, correlation_id.
- since and until (inclusive ISO-8601 UTC-normalized event-time bounds).

Results are always in ascending sequence order. The response includes entries, next_after_sequence, head_sequence, and has_more. The next request uses after_sequence=next_after_sequence. Appends after the first request do not reorder or invalidate already delivered records. A caller may set through_sequence to the observed head for a finite, repeatable traversal.

Filtering is applied without changing sequence identity. A cursor always advances to the last scanned sequence, including when filters exclude records, so clients do not loop forever over non-matching records. The response must make this behavior explicit and return the next cursor even for an empty filtered page when more source sequences remain.

A cursor beyond the current head is rejected. A cursor is not an authorization token or a snapshot of mutable state.

## 5. Lookup, replay, and export

Lookup by event ID returns the exact stored envelope. Replay is a read operation over an explicit sequence interval and yields records in ascending sequence order. Export uses the same envelope and ordering; it must not rewrite event IDs, producer timestamps, captured timestamps, payloads, or provenance.

## 6. Integrity verification

Verification checks, for each record in the requested interval:

1. JSON/envelope validity and required field types.
2. Unique event IDs and contiguous sequence numbers.
3. Canonical payload digest.
4. Entry digest.
5. Previous-entry hash linkage to the preceding record.
6. The first record's predecessor against a trusted checkpoint when verification begins after sequence 1.

A range verification response reports the checked range, verified count, first failing sequence, failure category, and assurance level. A partial range without a trusted starting checkpoint must not be reported as fully chain-verified. A normal checkpoint contains ledger_id, sequence, and entry_sha256. The POC may also issue a legacy-prefix checkpoint containing ledger_id, sequence, legacy_prefix_sha256, and legacy_prefix_sequence_count; ranges anchored there retain a legacy-unverified assurance. Checkpoint trust is external to the ledger API and depends on the caller retaining the checkpoint through a trusted channel. Full verification begins at sequence 1 or a trusted signed/externally retained checkpoint.

Legacy records are reported as legacy-unverified unless a verifiable migration/checkpoint procedure has established stronger evidence. Do not silently upgrade a legacy digest into a hash-chain claim.

## 7. Import and cross-ledger transfer

Import accepts an ordered batch with source ledger identity and source sequence metadata. The destination assigns its own local sequence while retaining source ledger ID/sequence in provenance. Event IDs and payload identity remain stable.

- Same event ID and same canonical payload: report duplicate; do not append again.
- Same event ID and different canonical payload: return conflict; do not mutate the existing record.
- Invalid order, malformed envelope, invalid digest, or failed source verification: reject the batch.
- Import is transactional per accepted batch or explicitly reports the committed prefix and retry cursor; it must never silently drop entries.

Cross-ledger transfer is pull from the source followed by import at the destination. It never consumes or deletes source records.

## 8. Compatibility and parity

The POC's existing GET /history and GET /history/{event_id} remain supported during migration. Existing JSONL records remain readable in original order. Ensemble's existing Memory event and append-once APIs remain supported; the ledger contract is a first-class resource, not a replacement for Memory recall/search.

Both repositories must run the same contract fixtures for append, duplicate/conflict, pull, cursor continuation, filters, lookup, replay, verification, import, and cross-implementation round trips. The contract must not expose SQLite, JSONL, ChromaDB, or other storage implementation details.