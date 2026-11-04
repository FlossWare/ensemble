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

**Foundation implemented:** SQLite now has a durable generation registry, a singleton active-generation pointer with a monotonically increasing epoch, and a single-building-generation constraint. Index, Documents, Vectors, Memory, and Knowledge checkpoints can be recorded and sealed. Cutover uses compare-and-swap against the expected active generation and epoch, and refuses activation unless all five checkpoints are sealed and acknowledged writes plus tombstones are explicitly verified. The in-memory adapter mirrors these state transitions.

**Vector shadow storage implemented:** migration 0017 adds generation-scoped vector records keyed by collection, generation, and record ID. The SQLite and in-memory adapters can write, query, and delete a shadow generation without changing active-generation vector query results. This is isolated storage only; it does not yet copy active vectors, coordinate writes, or activate a generation.

**Document shadow storage implemented:** migration 0018 adds generation-scoped document and chunk records, preserving source/revision identity and selecting the newest revision within each generation. SQLite and in-memory adapters expose generation-scoped chunk enumeration and lexical candidate search. Active document/chunk and FTS5 paths remain unchanged.

**Shadow tombstone handling implemented:** migration 0019 records source tombstones per generation. Shadow document writes reject tombstoned sources, document enumeration excludes them, and vector cleanup removes matching source records. Repeated tombstones are idempotent. This protects the shadow data plane when the rebuild process applies source-state changes.

**Bounded source enumeration implemented:** Index adapters now expose an ordered keyset cursor bounded by the source-ID upper key observed at the start of a pass. This avoids offset drift and gives a rebuild worker a finite key range. It is not a transactionally consistent snapshot: changes to existing source rows and inserts below the bound still require write-fence/reconciliation handling.

**Generation-scoped Index state implemented:** migration 0020 stores each source's revision sequence, revision ID, profiles, and tombstone state independently in the shadow generation. Writes are monotonic, tombstones are irreversible within a generation, and generation state can be paged by keyset cursor.

**Resumable Index source-copy pass implemented:** `IndexGenerationBuilder` copies one bounded source page per call, persists its key cursor and upper bound in the generation checkpoint, carries tombstones, and seals the Index checkpoint when the bounded pass completes. Re-instantiating the builder resumes from the durable checkpoint. This pass is idempotent, but the key range is not a consistent snapshot; write reconciliation remains mandatory.

**Resumable document/chunk copy pass implemented:** the builder requires the Index source pass to be sealed, then pages the generation-scoped source manifest and copies each active source's current document/chunks into isolated shadow tables. Tombstones are applied to both shadow document and vector stores. The Documents checkpoint persists its own cursor and counts and seals on completion. This still requires reconciliation for source changes that race the bounded copy.

**Resumable vector copy pass implemented:** the builder captures each active collection's model, dimensions, and record-key upper bound, then copies bounded pages into generation-scoped vector storage. Every record must resolve to a generation Index source; unattributed or unknown-source records fail closed, and tombstoned sources are skipped. The Vectors checkpoint records collection progress and counts and seals only after every captured collection completes.

**Durable Index change journal implemented:** migration 0021 records each accepted revision and tombstone in the same SQLite transaction as the authoritative Index mutation. Migration 0022 adds an explicit end-to-end acknowledgement marker. The owning ingestion/tombstone workflow marks the change acknowledged only after its required materializations complete. A new generation inherits any still-pending mutations from the active generation, closing the race where ingestion began just before rebuild creation. The journal supports bounded keyset replay; pending changes block replay and final fencing. The in-memory adapter mirrors the behavior.

**Final Index write fence implemented:** the Index can acquire a durable fence only when the caller's reconciled journal cursor matches the current journal high-water mark and no unacknowledged source mutations remain. While held, new Index revisions and tombstones are rejected with a retryable response. Generation activation now requires the fence, a matching reconciled watermark, and sealed checkpoints for Index, Documents, Vectors, Memory, Knowledge, and Index reconciliation. Aborting a build releases the fence.

**Acknowledgement REST contract implemented:** `POST /api/v1/index/changes/acknowledge` marks a source revision or tombstone complete after dependent capability writes have succeeded. The lower-level Index revision route does not acknowledge by itself; callers composing Index and other capability routes must acknowledge only after the full operation completes.

**Document/chunk reconciliation implemented:** acknowledged journal entries can be replayed into shadow Documents and Chunks using the latest active source revision. The builder verifies that the active document revision matches the reconciled Index source, applies generation tombstones, and persists an independent keyset checkpoint. A missing or mismatched active document fails closed.

**Vector reconciliation implemented:** acknowledged changes are replayed per active vector collection. Reconciliation removes the old shadow records for that source before copying current active records, and applies tombstones across collections. SQLite uses source-scoped JSON metadata lookup; a source with more than 10,000 vector records fails closed rather than being partially copied. The in-memory adapter mirrors the behavior.

**Knowledge reconciliation implemented:** migration 0023 adds generation-scoped Knowledge records. The builder copies Knowledge records alongside the bounded Index source manifest, verifies revision parity, and replays acknowledged revisions and tombstones with an independent durable checkpoint. SQLite and in-memory adapters support the same lifecycle.

**Still not complete:** Memory materialization reconciliation, REST rebuild orchestration, and restart recovery for a process that stops while the durable fence is held. The final fence and activation require all five capability reconciliation checkpoints, including Memory, so the rebuild API remains unavailable until those requirements are satisfied.

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
