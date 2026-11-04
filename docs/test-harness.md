# Test Harness

## Purpose

The Test Harness provides a controlled way to inject synthetic inputs, replay captured inputs, and exercise failure scenarios through Ensemble's existing REST contracts. It is a development and verification capability, not a production message broker or a second ingestion implementation.

The harness must test the same behavior that real clients use. It must not write directly to SQLite tables or call persistence adapters to manufacture successful outcomes.

## Operating boundary

- Disabled by default. Enabling the harness is an explicit deployment choice.
- Run only against a dedicated test deployment and database. Do not enable it on a deployment containing user or production data.
- Every run has a generated immutable run ID, a scenario name, a creation time, a lifecycle state, and an append-only event/result record.
- Every injected source is namespaced by run ID. The namespace is metadata and provenance, not an authorization boundary.
- Test data is never made safe merely by adding a `test=true` field. The dedicated deployment/database boundary is mandatory until Ensemble has end-to-end partition enforcement across ingestion, retrieval, Graph, Knowledge, vectors, and derived data.
- The harness invokes the same public REST operations as clients. It does not bypass validation, chunking, embeddings, indexing, tombstones, or retrieval.
- Cleanup is explicit, scoped to one run, and uses supported deletion/tombstone contracts. A cleanup failure leaves the run marked for cleanup and is visible to the caller.
- No automatic cleanup on process startup. Recovery must not silently delete test evidence.

## Modes

### Synthetic injection

Submit a document or event envelope through the normal REST API. The harness records the submitted envelope, request outcome, correlation ID, and resulting source/document identifiers.

### Replay

Replay an ordered sequence of previously captured envelopes. Replay preserves the original sequence and stable message identity by default. An explicit option may regenerate identities for a fresh run. Timing preservation is optional and bounded; default replay is immediate and deterministic.

### Fault scenarios

Fault behavior is applied at the harness boundary, not by corrupting persistent stores. Initial scenarios:

- `duplicate-message`: submit the same envelope more than once and verify idempotency.
- `out-of-order-events`: submit a newer revision before an older revision and verify stale-write rejection.
- `source-update-and-tombstone`: ingest, revise, tombstone, then attempt a late revision and verify the tombstone wins.
- `interrupted-ingestion`: stop between supported REST steps, record the partial outcome, then replay/recover.
- `generation-cutover-under-load`: reserved until generation rebuild has a complete write journal, enforced admission fence, recovery worker, and tested atomic cutover. The harness must not simulate a successful cutover using metadata-only fence markers.
- `recovery-after-restart`: persist an in-progress run, restart the application, and verify the run can be inspected and safely resumed or cleaned up.

## REST contract

The initial route family is:

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/v1/test/runs` | Create a named run or scenario |
| POST | `/api/v1/test/runs/{run_id}/inject` | Inject one synthetic envelope |
| POST | `/api/v1/test/runs/{run_id}/replay` | Replay an ordered batch |
| POST | `/api/v1/test/runs/{run_id}/scenarios/{scenario}/execute` | Execute a named scenario |
| GET | `/api/v1/test/runs` | List runs with bounded pagination and state filters |
| GET | `/api/v1/test/runs/{run_id}` | Inspect run state, events, and outcomes |
| DELETE | `/api/v1/test/runs/{run_id}` | Request scoped cleanup |

All mutating operations return a run ID and operation ID. Batch operations return per-envelope outcomes; a batch-level success must not conceal individual failures. Repeated requests with the same idempotency key return the recorded result rather than injecting the same operation again.

The exact request and response schemas are versioned with the REST contracts. The routes must return a clear disabled response when the feature flag is off.

## Run lifecycle

`created -> running -> completed`

A run may instead enter `failed`, `interrupted`, or `cleanup_pending`. Cleanup transitions to `cleaned` only after all owned sources and derived records have been removed or tombstoned through supported APIs and verified absent from retrieval. Run history remains available after cleanup; payload retention is configurable and bounded.

## Required safety and correctness checks

1. Feature disabled by default; no test routes mutate data when disabled.
2. Harness refuses to start unless the deployment is explicitly identified as a dedicated test environment.
3. A run cannot address or clean up a source outside its run namespace.
4. Replay ordering, idempotency, and per-message outcomes are observable.
5. A duplicate injection does not create duplicate logical records.
6. A tombstoned source cannot be resurrected by replay or late delivery.
7. Cleanup is idempotent and cannot affect another run.
8. Run metadata and interrupted state survive application restart.
9. Harness activity is visible in logs and has correlation IDs.
10. The normal application remains fully functional with the harness disabled.

## Implementation sequence

1. Establish the run, envelope, scenario, and result contracts plus safety boundary.
2. Add durable run metadata and idempotency tracking.
3. Add guarded REST routes that delegate to existing ingestion and deletion contracts.
4. Add synthetic injection, deterministic replay, and cleanup.
5. Add focused scenarios for duplicate delivery, out-of-order revisions, tombstones, and interrupted ingestion.
6. Add restart recovery tests.
7. Add generation-cutover scenarios only after generation consistency and recovery are complete.

The Test Harness remains a capability within the single Ensemble application and REST server. It does not introduce a broker, worker fleet, federation, or a second persistence system.
