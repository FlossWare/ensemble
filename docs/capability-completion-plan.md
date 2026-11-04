# Ensemble Capability Completion Plan

## Objective

Complete the intended Ensemble platform as a standalone, client-independent REST service. This plan is functionality-first. The historical POC and Claude Ensemble repositories are reference material for discovering useful behavior and requirements; reproducing their internal designs, test counts, or test suites is not a goal.

## Working rules

- Implement the capability Ensemble needs, using its own contracts and architecture.
- Keep one application and one REST server unless a demonstrated operational requirement justifies a separate process.
- REST is canonical. MCP and coding-agent integrations remain adapters outside core Ensemble.
- SQLite remains Ensemble's only persistence implementation.
- Preserve existing working behavior and migrations.
- During this phase, prioritize implementation and end-to-end workflows over expanding the test suite.
- Continue running the existing tests when practical. Fix regressions introduced by implementation; do not knowingly leave the repository broken.
- Do not create tests merely to mirror POC tests. Defer systematic test expansion until the capability set is substantially complete.
- Update implementation status and this plan as capabilities become usable, not merely when routes or stubs exist.

## Completed in the current review branch

- All supported document, lexical, transcript, PDF, URL, and Knowledge ingestion paths now pass through Index revision decisions.
- Knowledge publication writes the durable document/chunk index, Knowledge provenance record, and optional knowledge_chunks vectors.
- Accepted source revisions are associated with exact extracted entities in Graph. Hybrid retrieval expands through Graph-linked source relationships and reranks after candidate fusion.
- Source tombstones remove content from the active document retrieval view and prune vectors across all collections. Knowledge-only vector retrieval also checks each candidate against the current Index revision and tombstone state.
- Initial client-independent Context assembly composes Hybrid retrieval, Graph expansion, Memory documents, and optional execution-lineage recall. It preserves source provenance and applies a bounded extractive context budget.
- Review-branch CI currently verifies the existing suite plus new source lifecycle, Graph expansion, and Context assembly coverage. Token estimates are character-based approximations, not model-tokenizer exact counts.

These items are implemented foundations, not claims that every operational requirement in the sections below is complete.

## Priority 0: make the core platform complete end to end

### 1. Complete hybrid retrieval

- Retrieve from the shared document/chunk index populated by Knowledge, transcripts, and documents; independent lexical/vector channel diagnostics and candidate counts are implemented. The optional ranking-provider adapter and reusable reranking decorator are now implemented; calibrated channel weighting, bounded retrieval deadlines/cancellation, and full trace provenance remain.
- Preserve source identity, revision, scope, and provenance through candidate fusion and reranking.
- Define deterministic fusion, deduplication, and stable tie-breaking.
- Make partial-success and degraded-dependency behavior explicit. Search responses now distinguish healthy, partial, and unavailable retrieval, preserve a healthy channel when its peer fails, and report vector collection failures separately from usable document-chunk vectors.
- Ensure a failed or partial retrieval is never represented as a successful empty result. The response now includes per-channel status, availability, candidate counts, and stable degraded dependency identifiers.
- Add bounded retrieval deadlines and cancellation behavior. Per-channel deadlines are implemented with `ENSEMBLE_RETRIEVAL_CHANNEL_TIMEOUT` (default 5 seconds, clamped to 50 ms–60 seconds). Graph expansion has its own `ENSEMBLE_RETRIEVAL_GRAPH_TIMEOUT` (default 2 seconds, clamped to 50 ms–30 seconds). Timed-out channels degrade independently and preserve usable peer-channel results. Channel diagnostics now report elapsed time, including embedding, external vector lookup, local document-vector scan, and Graph expansion.
- Ensure retrieval scope is enforced consistently across all channels (requires authentication/partition enforcement).

### 2. Complete transcript ingestion lifecycle

Implemented on current main:
- Durable transcript ingestion jobs persist source identity, revision, byte checkpoints, operation state, and failure details.
- Retries are idempotent; checkpoints advance only after durable indexing succeeds.
- Interrupted jobs recover on application restart.
- The JSONL parser handles complete newline-terminated records, malformed records, UTF-8 byte offsets, and incomplete trailing records.

Remaining:
- The parser still accepts caller-supplied byte slices. Filesystem scanning remains caller-authorized and scope-restricted; Ensemble must not open arbitrary server-side paths.
- Extend durable job tracking to other long-running ingestion and rebuild operations.

### 3. Complete source and document lifecycle correctness

- Define canonical source identity and revision semantics across documents, files, URLs, and transcripts.
- Ensure stale updates cannot replace newer active content.
- Ensure tombstones cannot be undone by delayed or retried upserts; Index now gates supported ingestion routes and tombstones remove active document/vector results.
- Make freshness verification explicit before returning retrieval results.
- Preserve provenance and source metadata through chunking, indexing, Knowledge publication, and retrieval.
- Make all lifecycle operations safe across restart and retry.

### 4. Complete Memory as a usable shared capability

- Finish scoped source import/reconciliation with explicit authorization and safe path handling.
- Complete execution-context-aware recall and useful retrieval ranking.
- Extend the initial bounded Context assembly with exact model-tokenizer budgeting when a concrete tokenizer is configured; current character-based estimates and extractive compression are deterministic and provenance-preserving.
- Conflict and append-once semantics are enforced with payload-digest comparison. Optional timezone-aware event expiry now excludes expired events from recall while retaining ledger history; explicit named-memory deletion hard-deletes its content, entries, append-once keys, and imported-source provenance.
- Ensure Memory readiness reflects usable persisted state and recovery, not just process liveness.

## Priority 1: operationally safe and useful platform behavior

### 5. Index generation rebuild and cutover

Implemented on `integration/generation-mainline-2026-11-04` (not yet merged):
- REST operations create and inspect a rebuild, perform bounded copy passes for Index, Documents, Vectors, Memory, and Knowledge, reconcile each capability, acquire the write fence, activate, and abort.
- Memory now has generation-scoped durable snapshots and a mutation journal. Memory writes are blocked while the shared cutover fence is held.
- The Index journal tracks acknowledged mutations. Completed document/Knowledge writes and source tombstones acknowledge only after the corresponding stores are updated.
- The active generation pointer now drives lexical/vector retrieval. Document writes, vector writes, Knowledge publication, Index manifest changes, Memory snapshots, and tombstones keep the active generation current after cutover.
- The existing FTS5/BM25 lexical scoring path is preserved; active-generation candidate IDs constrain returned results.
- Activation retries are idempotent after a lost response. Restart recovery releases an abandoned fence and reopens reconciliation checkpoints so subsequent writes can be reconciled.
- CI has verified end-to-end copy, reconciliation, activation, retrieval, post-activation document/Knowledge/vector writes, tombstones, Memory snapshot reconciliation, idempotent activation retry, and fence recovery. The latest verified integration run passed 203 tests with 1 skipped.

Still required before merge:
- Verify the latest integration head's CI result and review the crash/recovery boundaries and migration compatibility.
- Complete failure-injection coverage for interruption between fence acquisition and activation, plus active-generation Memory and Knowledge lifecycle edge cases.
- Keep activation fail-closed if any capability checkpoint, journal acknowledgement, or tombstone verification is missing.

Do not ship a rebuild operation that can silently lose acknowledged writes or resurrect deleted content.

### 6. Context assembly and compression

- Provide a client-independent context assembly contract.
- Support bounded token budgets and configurable selection policies.
- Preserve source attribution and provenance.
- Support deterministic truncation and compression summaries.
- Distinguish omitted, truncated, summarized, and fully included content.
- Keep provider-specific prompt-cache optimization outside core correctness requirements.

### 7. Runtime resilience

- Define absolute request deadlines and cancellation propagation.
- Bound retries and prevent retry amplification.
- Avoid nested REST dispatch starvation within the single application.
- Define explicit partial-result, empty-result, unavailable, and not-ready semantics.
- Add durable operation tracking for long-running ingestion and rebuild jobs.
- Provide recovery and reconciliation for interrupted operations.

### 8. Vector and model capability completion

- Decide when exact vector scans cease to be acceptable and implement an appropriate local index when justified.
- Complete model identity, revision, dimensions, normalization, and provider readiness reporting.
- Make embedding and reranking provider configuration/availability/degradation visible in capability health; remote ranking health is configuration-reported and actual provider failures are surfaced per request.
- Keep hardware-specific acceleration optional and report backend selection accurately.

### 9. Source adapters

- Complete bounded website crawling with robots policy, DNS-to-connection binding, redirect and response limits, and durable refresh jobs.
- Add OCR only with a deliberate provider/runtime decision.
- Provide source connector contracts without coupling core ingestion to individual vendors.

## Priority 2: usability and production readiness

### 10. Finish Control Center

- Complete diagnostics and operational visibility for all capabilities.
- Add durable operation/job inspection and recovery controls.
- Expose model/provider configuration safely.
- Provide clear source lifecycle, freshness, and rebuild status.
- Keep the Control Center a REST consumer; no direct database access or UI-only backend logic.

### 11. Security and deployment

Implemented in the partition-isolation branch:

- Bearer-token authentication with principal-to-partition allow-lists.
- Partition selection is separate from authorization; a client-supplied partition header cannot grant access.
- Physical SQLite database isolation across every persistent capability, with automatic migration and identity verification for named partitions.
- Internal REST propagation of the authenticated partition and credential.
- Local unauthenticated mode restricted to the default partition; non-loopback listeners require configured partition credentials.
- Control Center credential entry uses tab-scoped browser session storage.

Remaining before production readiness:

- Broader adversarial tests for all capability paths, deletion, replay, and recovery.
- Backup/restore and retention verification across the full partition directory.
- Credential rotation/revocation procedures and audit trails.
- Rate limits, resource bounds, production observability, and actionable security diagnostics.
- Deployment and upgrade hardening for multiple named partitions.

## Deferred work

- Systematic expansion of unit, integration, failure-injection, and coverage tests.
- POC test-suite reproduction or test-count matching.
- Provider prompt-cache optimization.
- Client-specific hooks and prompt injection; these belong in client adapters such as `ensemble-claude`, not Ensemble core.
- Separate processes or microservices for logical capabilities without demonstrated need.

## Completion standard

A capability is complete when it has a usable contract, a working implementation, durable behavior where state is involved, clear failure/degradation semantics, and is connected to the end-to-end workflows that depend on it. A route stub, UI panel, or isolated algorithm is not by itself a completed capability.

Existing tests should continue to be run when practical. New test development is deliberately deprioritized during this functionality phase, but regressions and known failures must not be concealed.

## Current review-branch state

The following work is implemented in the current review branch, with CI verification still required for the latest commit:

- Knowledge publication is Index-gated and writes the shared document/chunk index, Knowledge provenance store, optional vector collection, and exact entity/source Graph links.
- Supported document, file, PDF, URL, transcript, lexical, and Knowledge ingestion paths consult the Index revision authority before writing.
- Hybrid Search combines the shared lexical/vector document candidates, enriches exact query entities from Graph-linked sources, retains source/revision/chunk provenance, and can rerank the combined candidate set.
- Source tombstones deactivate current document chunks, tombstone Knowledge, and remove source vectors across collections. The Index tombstone remains authoritative and blocks later revisions.
- Memory context assembly is exposed as a REST capability with deterministic ordering, approximate character-based token budgeting, extractive compression, and source metadata.
- System configuration reports listener and durable-persistence status without exposing filesystem paths or database URLs.

These are implemented slices, not a declaration that the platform is production-ready. Transcript parsing still relies on caller-supplied byte slices, while durable transcript jobs, byte checkpoints, retry state, and restart recovery are implemented. Server-side filesystem scanning remains intentionally out of scope without an explicit scoped-source contract.

## Remaining implementation sequence

1. Finish and merge the generation rebuild/cutover slice after current-head CI and failure-recovery review.
2. Close remaining source lifecycle gaps: cleanup retry state, stale vector reconciliation, and failure recovery across Index, Documents, Knowledge, Graph, and Vector.
3. Complete Memory scoped-source reconciliation, event lifecycle semantics, conflict/deletion policy, and retrieval quality.
4. Add bounded deadlines/cancellation and durable operation tracking for long-running non-transcript ingestion and rebuild operations.
5. Finish Control Center usability and real execution diagnostics, while keeping all operations REST-backed.
6. Complete operational readiness: backup/restore, migrations/upgrade, observability, rate limits, authentication/authorization, and partition enforcement.
7. Verify the local embedding/reranking runtime matrix and decide when exact vector scans require a local ANN implementation.
