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
- Source tombstones remove content from the active document retrieval view and prune Knowledge vectors.
- Initial client-independent Context assembly composes Hybrid retrieval, Graph expansion, Memory documents, and optional execution-lineage recall. It preserves source provenance and applies a bounded extractive context budget.
- Review-branch CI currently verifies the existing suite plus new source lifecycle, Graph expansion, and Context assembly coverage. Token estimates are character-based approximations, not model-tokenizer exact counts.

These items are implemented foundations, not claims that every operational requirement in the sections below is complete.

## Priority 0: make the core platform complete end to end

### 1. Complete hybrid retrieval

- Retrieve from the shared document/chunk index populated by Knowledge, transcripts, and documents; independent lexical/vector channel diagnostics and candidate counts are now implemented. External ranking fallback remains.
- Preserve source identity, revision, scope, and provenance through candidate fusion and reranking.
- Define deterministic fusion, deduplication, and stable tie-breaking.
- Make partial-success and degraded-dependency behavior explicit. Search responses now distinguish healthy, partial, and unavailable retrieval, preserve a healthy channel when its peer fails, and report vector collection failures separately from usable document-chunk vectors.
- Ensure a failed or partial retrieval is never represented as a successful empty result. The response now includes per-channel status, availability, candidate counts, and stable degraded dependency identifiers.
- Add bounded retrieval deadlines and cancellation behavior. Per-channel deadlines are implemented with `ENSEMBLE_RETRIEVAL_CHANNEL_TIMEOUT` (default 5 seconds, clamped to 50 ms–60 seconds). Graph expansion has its own `ENSEMBLE_RETRIEVAL_GRAPH_TIMEOUT` (default 2 seconds, clamped to 50 ms–30 seconds). Timed-out channels degrade independently and preserve usable peer-channel results. Channel diagnostics now report elapsed time, including embedding, external vector lookup, local document-vector scan, and Graph expansion.
- Ensure retrieval scope is enforced consistently across all channels (requires authentication/partition enforcement).

### 2. Complete transcript ingestion lifecycle

- Turn the existing incremental JSONL parser into a durable ingestion workflow; the current caller-supplied byte-slice parser and synchronous session ingestion are not durable jobs.
- Persist source identity, revision, byte checkpoint, operation state, and failure details.
- Make retries idempotent and prevent acknowledgement before durable indexing succeeds.
- Resume safely after process restart.
- Handle changed source revisions, truncation, malformed records, and incomplete trailing records.
- Keep filesystem access caller-authorized and scope-restricted; do not allow arbitrary server-side paths.

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
- Define conflict, append-once, expiry, and deletion semantics.
- Ensure Memory readiness reflects usable persisted state and recovery, not just process liveness.

## Priority 1: operationally safe and useful platform behavior

### 5. Index generation rebuild and cutover

Implement durable index rebuilds that can safely coexist with writes:

- Create a new index generation.
- Reconcile Memory and Knowledge independently.
- Establish durable sealed checkpoints.
- Acquire and register write fences with the owning capabilities.
- Verify all acknowledged changes are represented.
- Atomically switch the active generation.
- Release fences and persist completion.
- Recover safely from crashes, lost responses, expired fences, and uncertain cutover.

**Foundation implemented:** SQLite now has a durable generation registry, a singleton active-generation pointer with a monotonically increasing epoch, and a single-building-generation constraint. Index, Memory, and Knowledge checkpoints can be recorded and sealed. Cutover uses compare-and-swap against the expected active generation and epoch, and refuses activation unless all three checkpoints are sealed and acknowledged writes plus tombstones are explicitly verified. The in-memory adapter mirrors these state transitions.

**Vector shadow storage implemented:** migration 0017 adds generation-scoped vector records keyed by collection, generation, and record ID. The SQLite and in-memory adapters can write, query, and delete a shadow generation without changing active-generation vector query results. This is isolated storage only; it does not yet copy active vectors, coordinate writes, or activate a generation.

**Document shadow storage implemented:** migration 0018 adds generation-scoped document and chunk records, preserving source/revision identity and selecting the newest revision within each generation. SQLite and in-memory adapters expose generation-scoped chunk enumeration and lexical candidate search. Active document/chunk and FTS5 paths remain unchanged.

**Not yet implemented:** source tombstone propagation into shadow generations, a rebuild worker to populate all shadow stores, write-fence acquisition and propagation into each writer, acknowledged-write reconciliation, automatic restart recovery, and end-to-end atomic cutover of actual retrieval data. Keep the rebuild API unavailable until those pieces exist.

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
- Make embedding and reranking availability/degradation visible in capability health.
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

- Define authentication and authorization boundaries.
- Enforce scope/partition isolation across ingestion, Memory, Knowledge, Graph, Vector, and retrieval.
- Add rate limits and resource bounds.
- Complete installation, upgrade, backup, restore, and recovery workflows.
- Add production observability and actionable diagnostics.

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

These are implemented slices, not a declaration that the platform is production-ready. In particular, transcript parsing still relies on caller-supplied byte slices; it does not yet own durable ingestion jobs or restart checkpoints.

## Remaining implementation sequence

1. Durable transcript ingestion jobs with persisted byte checkpoints, retries, revision handling, and restart recovery.
2. Close remaining source lifecycle gaps: cleanup retry state, stale vector reconciliation, rebuild/reindex semantics, and failure recovery across Index, document, Knowledge, Graph, and Vector stores.
3. Complete Memory scoped-source reconciliation, event lifecycle semantics, conflict/deletion policy, and retrieval quality.
4. Add bounded deadlines/cancellation and durable operation tracking for long-running ingestion and rebuild operations.
5. Finish Control Center usability and real execution diagnostics, while keeping all operations REST-backed.
6. Complete operational readiness: backup/restore, migrations/upgrade, observability, rate limits, authentication/authorization, and partition enforcement.
7. Verify the local embedding/reranking runtime matrix and decide when exact vector scans require a local ANN implementation.
