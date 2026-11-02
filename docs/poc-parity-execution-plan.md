# POC Parity Execution Plan

This plan converts the source inventory in [POC capability parity](poc-parity.md) into reviewable engineering slices. A capability is reviewable only when its source audit, contract behavior, regression tests, and documented deviations are available. Matching route names are not evidence of parity.

## Review gate 0: establish a trustworthy baseline

- [x] Compare Control Center requests with the live FastAPI route signatures for Chunking, Tokenization, Extraction, Intent, Graph, and Knowledge.
- [x] Correct the Pipeline Inspector's invalid `default` profile fallback. The actual defaults are `window-v1` and `lexical-v1`.
- [ ] Run the complete Ensemble test suite and record the exact commit and result. Existing PR #3 records a passing earlier CI run, but it does not cover the current parity branch commits.
- [ ] Run static checks and inspect the GitHub Actions result for the branch.
- [ ] Verify all new Control Center panels against a running local Ensemble instance. A successful GitHub file update is not a browser test.
- [x] Add a regression test proving a transcript retry with a changed timestamp does not duplicate the event or vector document.

## Review slice 1: Memory parity

**Priority: P0**

The current SQLite Memory implementation is intentionally narrower than the POC file-backed Memory authority. The POC source audit must cover `services/memory/memory_service.py`, its client, execution-context model, and the complete Memory test suite.

Required behavior to prove or explicitly disposition:

- Append-once event ledger: stable event IDs, canonical payload digest, duplicate retry, conflicting reuse, malformed JSONL fail-closed behavior, newline framing, flush/fsync durability, and concurrent writers.
- Execution-aware recall: parent, ancestor, same-request, and related-lineage ordering; descendants excluded; invalid context records skipped.
- Search: exact identifier matching outranks related prose; unknown identifiers do not produce false positives; external file edits invalidate cached indexes.
- File operations: validated memory names, path containment, symlink-safe traversal, incremental change detection, bounded warmup/retry, and private runtime/socket permissions.
- Source reconciliation: define an explicit REST contract for caller-authorized roots and file metadata. Never let a remote caller supply an arbitrary path that Ensemble opens without an allowlisted scope and symlink-safe validation.
- Readiness must represent usable persistence/index state, not merely process liveness.

The current Ensemble implementation already has SQLite-backed records/events, append-once semantics, context recall, exact-identifier-aware deterministic term-vector search, and REST endpoints. Its deterministic term-vector search is not neural semantic search. Caller-supplied-content import is a deliberate trust-boundary adaptation, not direct filesystem parity. Path validation now rejects traversal and symlink aliases outside the declared scope; regression tests cover both. Do not replace these distinctions with claims of full filesystem or neural semantic parity.

**Exit criteria:** source-to-test traceability, explicit accepted deviations, and tests for idempotency/conflict/concurrency/restart, execution-context ranking, exact-identifier search, and secure source reconciliation.

## Review slice 2: Hybrid retrieval and transcript ingestion

**Priority: P0**

Compare `services/embeddings/hybrid_service.py` and `test_hybrid_service.py` with Ensemble's Hybrid, Lexical, Vector, Index, Knowledge, Extraction, Intent, Ranking, and Graph paths.

Required behavior to prove:

- Independent lexical and vector legs; partial-success results and accurate degraded dependency reporting.
- Knowledge and transcript candidates retain source-specific provenance through fusion and reranking.
- RRF ordering, score contributions, duplicate suppression, and stable tie-breaking.
- Transcript parsing for text, thinking, tool use, tool results, touches, timestamps, subagents, clipping, malformed lines, and partial final lines.
- Incremental offset resume, prompt/event deduplication, ingestion checkpoints, and no false acknowledgement of unindexed data. Ensemble currently accepts already-parsed session/message payloads; the POC raw JSONL parser, byte-offset checkpoint, complete-line handling, and filesystem root/symlink validation are not yet ported.
- Transcript root containment and symlink rejection.
- Bounded cache behavior; never cache partial or failed retrieval results.
- Related-session lookup from exact file/path identifiers.

The current README explicitly says complete transcript/session ingestion and complete hybrid channel orchestration remain open. Treat these as P0 parity gaps, not dashboard work.

**Exit criteria:** focused regression tests for each behavior above and an end-to-end test that ingests a transcript incrementally, retrieves its content through lexical and vector channels, and preserves provenance under degraded dependencies.

## Review slice 3: model-backed retrieval behavior

**Priority: P1**

- Embedding: pin and report model revision, dimensions, document/query prefixes, masked pooling, normalization, CPU/GPU backend selection, fail-closed GPU behavior, and numerical parity. GPU hardware tests remain opt-in and must be reported separately from ordinary CI.
- Reranking: score alignment, stable ordering, bounded inputs, serialized model access, and readiness failure when the model is unavailable.
- Vector: exact metric/order semantics, deterministic upsert, persistence/reload, dimension and finite-value validation, collection/model compatibility, and bounded batches.
- Lexical: FTS5/BM25 semantics, stemming, shared tokenization, incremental idempotent ingest, deduplication, and fail-closed tokenization dependency.

**Exit criteria:** contract tests plus model-dependent tests clearly separated from ordinary CI. Do not call CPU-only CI proof of Intel GPU parity.

## Review slice 4: deterministic processing and source lifecycle

**Priority: P1**

- Chunking: stable IDs, profile-version isolation, exact Unicode offsets, overlap coverage, Markdown heading provenance, and message clipping.
- Tokenization: Unicode case folding, minimum token length, compound identifiers, UUID handling, and exact offsets.
- Extraction and Intent: exact spelling/offsets, uncertainty/confidence distinction, evidence-backed labels, no-evidence behavior, and deterministic bounds.
- Index and Knowledge: monotonic revision decisions, duplicate/conflict/superseded behavior, profile-version conflicts, tombstone permanence, Index-gated publication, and persistence across restart.

**Exit criteria:** regression tests map directly to the POC source/test cases and demonstrate restart behavior where persistence is involved.

## Review slice 5: operational and optimization capabilities

**Priority: P2**

- Graph: operation semantics and persistence/reconstruction.
- Standalone Thompson: sampling, cost filtering/fallback, outcome idempotency, and explicit non-implementation of POC task/capability filtering.
- Learning: retry-safe checkpoint progression, Memory/Thompson side effects, artifact schema and idempotency, reset semantics, and portable prior behavior.
- GA: evaluator-specific behavior, reproducibility, scheduler/recovery, artifact export, and explicit promotion semantics. Synthetic evaluator results must not be presented as production quality.

## Code review cadence

Start review as soon as a slice meets its exit criteria. Do not wait for all five slices. Each review should include:

1. POC source and test references.
2. Ensemble implementation and test references.
3. Behavior-by-behavior parity table: matched, partial, missing, or intentionally different.
4. Exact test commands and CI run links/results.
5. Security, persistence, failure-mode, and compatibility notes.
6. A short list of remaining gaps, each tied to an issue or follow-up commit.

The immediate engineering order is Memory parity tests and Hybrid transcript-ingestion parity. Control Center work continues only when it exposes a real diagnostic or review need.


## Implementation update: ingestion adapters

The branch now includes a pure JSONL transcript parser and REST ingestion route at POST /api/v1/ingest/transcripts. It accepts caller-supplied content slices, extracts text/thinking/tool-use/tool-result records, detects file touches, consumes only complete newline-terminated records, computes UTF-8 byte offsets, and returns the next checkpoint. It does not open server-side filesystem paths or persist checkpoints; durable job/checkpoint coordination remains open. Regression coverage was added for multibyte UTF-8 offsets, incomplete tails, malformed complete lines, event extraction, and retry idempotency.

A bounded PDF adapter is also present at POST /api/v1/ingest/files. It extracts page-aware text and sends it through the existing document ingestion path. Limits are 20 MiB, 1000 pages, and 4 million extracted characters. Encrypted and textless/scanned PDFs are rejected; OCR is not implemented. Public HTML/plain-text URI preview and explicit single-page ingestion already existed; broad crawling, robots policy, DNS connection binding, refresh jobs, and JavaScript rendering remain open.

These are implementation commits, not a claim of passing tests. The branch has not yet produced a verifiable CI run in this workflow.
