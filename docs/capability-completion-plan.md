# Ensemble Capability Completion Plan

## Objective

Complete the intended Ensemble platform as a standalone, client-independent REST service. This plan is functionality-first. The historical POC and Claude Ensemble repositories are reference material for discovering useful behavior and requirements; reproducing their internal designs, test counts, or test suites is not a goal.

## Working rules

- Implement the capability Ensemble needs, using its own contracts and architecture.
- Keep one application and one REST server unless a demonstrated operational requirement justifies a separate process.
- REST is canonical. MCP and coding-agent integrations remain adapters outside core Ensemble.
- SQLite remains Ensemble's only persistence implementation.
- Preserve existing working behavior and migrations.
- Implement capabilities and their unit/integration tests together. End-to-end behavior is the completion criterion, not route presence.
- Run the full test suite for every meaningful change. Add regression tests for every fixed defect and failure mode.
- Do not mirror POC tests mechanically. Derive tests from Ensemble contracts, acceptance criteria, failure modes, persistence boundaries, and end-to-end workflows.
- Update implementation status and this plan as capabilities become usable, not merely when routes or stubs exist.

## Completed on main

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
- Implement Reciprocal Rank Fusion (RRF) as an explicit, provider-neutral retrieval fusion stage. Deterministic RRF now runs across lexical, vector, and Graph-expanded candidate channels; the reusable fusion contract also accepts other ranked channels. The smoothing constant is configurable, candidates deduplicate by stable identity, and tie-breaking is deterministic.
- Preserve each channel's original rank and expose per-channel RRF contributions in retrieval trace diagnostics. A reusable `/api/v1/ranking/fuse` endpoint is available. Weighted RRF is part of the initial capability: static weights are configured with `ENSEMBLE_RRF_WEIGHTS`, while Thompson-adaptive weights are enabled with `ENSEMBLE_RRF_WEIGHT_STRATEGY=thompson` and use persisted `retrieval:<channel>` outcomes through the Thompson capability. Record a channel judgment idempotently through `/api/v1/search/feedback` using channel values such as `lexical`, `vector`, and `graph`. Keep the fusion policy provider-neutral and independently testable; do not couple it to storage, embedding, or reranking providers.
- A reusable offline evaluation harness now computes Recall@k, MRR@k, and nDCG@k for lexical-only, vector-only, unweighted RRF, weighted RRF, and Thompson runs from a shared qrels fixture. Populate representative judged fixtures and use those results to compare the strategies before enabling adaptive weighting by default.
- Define deterministic fusion, deduplication, and stable tie-breaking.
- Make partial-success and degraded-dependency behavior explicit. Search responses now distinguish healthy, partial, and unavailable retrieval, preserve a healthy channel when its peer fails, and report vector collection failures separately from usable document-chunk vectors.
- Ensure a failed or partial retrieval is never represented as a successful empty result. The response now includes per-channel status, availability, candidate counts, and stable degraded dependency identifiers.
- Add bounded retrieval deadlines and cancellation behavior. Per-channel deadlines are implemented with `ENSEMBLE_RETRIEVAL_CHANNEL_TIMEOUT` (default 5 seconds, clamped to 50 ms–60 seconds). Graph expansion has its own `ENSEMBLE_RETRIEVAL_GRAPH_TIMEOUT` (default 2 seconds, clamped to 50 ms–30 seconds). Timed-out channels degrade independently and preserve usable peer-channel results. Channel diagnostics now report elapsed time, including embedding, external vector lookup, local document-vector scan, and Graph expansion.
- Retrieval scope is enforced by bearer-token principal-to-partition authorization and physically isolated SQLite stores. Internal REST composition forwards the authenticated scope. Regression coverage now verifies Graph-expanded Hybrid candidates, reranker inputs, and Knowledge vector retrieval cannot cross partitions; the REST response assertions are paired with direct store isolation tests. Remaining work is adversarial coverage for diagnostic metadata and degraded-channel edge cases.

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

- Scoped source import/reconciliation uses canonical caller-supplied paths, rejects traversal and symlink aliases, constrains stale marking to the requested source and scope, and never scans server-side filesystems. REST integration tests now verify reconciliation in one scope does not affect another scope or another authenticated partition. Event lifecycle coverage includes idempotent event IDs, conflicting payload rejection, expiry exclusion from recall while retaining ledger history, and explicit deletion.
- Complete execution-context-aware recall and useful retrieval ranking.
- Memory keyword and semantic search now share the provider-neutral RRF implementation rather than combining incomparable raw scores. Results retain each channel's rank, raw score, and RRF contribution for inspection.
- Context assembly now supports exact model-tokenizer budgeting when `ENSEMBLE_CONTEXT_TOKENIZER_PATH` points to a configured Hugging Face `tokenizers` JSON file (install the `context-tokenizer` extra). Without that explicit configuration, it retains the deterministic character/4 approximation and reports that it is approximate. Both paths use the selected counter for the complete assembled context and preserve source provenance during extractive compression.
- Conflict and append-once semantics are enforced with payload-digest comparison. Optional timezone-aware event expiry now excludes expired events from recall while retaining ledger history; explicit named-memory deletion hard-deletes its content, entries, append-once keys, and imported-source provenance.
- Ensure Memory readiness reflects usable persisted state and recovery, not just process liveness.

## Priority 1: operationally safe and useful platform behavior

### 5. Index generation rebuild and cutover

Merged into `main` via [PR #42](https://github.com/FlossWare/ensemble/pull/42):
- REST operations create and inspect a rebuild, perform bounded copy passes for Index, Documents, Vectors, Memory, and Knowledge, reconcile each capability, acquire the write fence, activate, and abort.
- Memory now has generation-scoped durable snapshots and a mutation journal. Memory writes are blocked while the shared cutover fence is held.
- The Index journal tracks acknowledged mutations. Completed document/Knowledge writes and source tombstones acknowledge only after the corresponding stores are updated.
- The active generation pointer now drives lexical/vector retrieval. Document writes, vector writes, Knowledge publication, Index manifest changes, Memory snapshots, and tombstones keep the active generation current after cutover.
- The existing FTS5/BM25 lexical scoring path is preserved; active-generation candidate IDs constrain returned results.
- Activation retries are idempotent after a lost response. Restart recovery releases an abandoned fence and reopens reconciliation checkpoints so subsequent writes can be reconciled.
- CI has verified end-to-end copy, reconciliation, activation, retrieval, post-activation document/Knowledge/vector writes, tombstones, Memory snapshot reconciliation and active-generation mirroring, idempotent activation retry, startup fence recovery (including repeated startup), incomplete-activation rejection, transaction rollback under injected activation failure, and retry after active-generation Vector mirror failure. The latest verified integration run passed 212 tests with 1 skipped.

Remaining follow-up:
- Continue failure-injection coverage across the remaining capability write paths and interrupted ingestion. Repeated startup recovery and atomic activation rollback are now covered.
- Extend durable operation tracking to long-running rebuild and non-transcript ingestion jobs.
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

- Bounded same-origin crawl preview enforces robots.txt, pins each connection to its validated DNS address, rejects cross-origin redirects, and caps page attempts, depth, response size, and total extracted characters. Durable SQLite crawl jobs persist requests/results, run through a restart-recoverable worker, and expose status/list/retry REST operations. Explicit batch ingestion persists a stable revision and per-page state before each write, checkpoints successful pages, skips completed pages on replay, and retries failed/interrupted pages without allocating a new revision. Startup recovery marks interrupted per-page writes for retry. Recurring source schedules atomically dispatch durable crawl jobs that automatically ingest refreshed pages; schedules can be paused, resumed, and deleted. Control Center exposes queue/status/retry/ingest and schedule management. Partitioned deployments must configure `ENSEMBLE_INTERNAL_BEARER_TOKEN` as a worker principal authorized for each scheduled partition.
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

- POC test-suite reproduction or test-count matching.
- Provider prompt-cache optimization.
- Client-specific hooks and prompt injection; these belong in client adapters such as `ensemble-claude`, not Ensemble core.
- Separate processes or microservices for logical capabilities without demonstrated need.

## Completion standard

A capability is complete when it has a usable contract, a working implementation, durable behavior where state is involved, clear failure/degradation semantics, and is connected to the end-to-end workflows that depend on it. A route stub, UI panel, or isolated algorithm is not by itself a completed capability.

Unit, integration, migration, restart/recovery, concurrency, and failure-injection tests are part of the implementation, not post-parity cleanup. Every issue must have tests mapped to its acceptance criteria. The full suite must pass before the issue is closed or the work is presented for review.

## Current source-lifecycle integration work

Branch: `integration/source-lifecycle-mainline-2026-11-04` (not yet merged).

- Search results in both base and generation modes are filtered against authoritative Index revision, tombstone, and permanent-purge state.
- Graph expansion and Knowledge retrieval reject permanently purged sources as well as stale or tombstoned revisions.
- Index tombstones enqueue durable reclamation jobs even when immediate downstream cleanup is partial.
- The reclamation worker physically removes active materialized data, then marks the Index source permanently purged. Purge marking is idempotent.
- Control endpoints expose reclamation queue status and an operator-triggered bounded pass.
- Direct document writes require a matching current Index revision; direct document/Knowledge/vector source tombstones require the Index tombstone lifecycle.
- Standalone vector upsert, metadata, and prune operations are paused during generation rebuilds. Normal indexed ingestion remains available and is reconciled through the Index change journal.
- Latest source-lifecycle integration snapshot passed 271 tests with 1 skipped and 1 warning. The current PR's latest Python workflow also passed after the additional partition-recovery and robots-delay regression tests.

## Current main state

The following work is implemented in the current review branch, with CI verification still required for the latest commit:

- Knowledge publication is Index-gated and writes the shared document/chunk index, Knowledge provenance store, optional vector collection, and exact entity/source Graph links.
- Supported document, file, PDF, URL, transcript, lexical, and Knowledge ingestion paths consult the Index revision authority before writing.
- Hybrid Search combines the shared lexical/vector document candidates, enriches exact query entities from Graph-linked sources, retains source/revision/chunk provenance, and can rerank the combined candidate set.
- Source tombstones deactivate current document chunks, tombstone Knowledge, and remove source vectors across collections. The Index tombstone remains authoritative and blocks later revisions.
- Memory context assembly is exposed as a REST capability with deterministic ordering, approximate character-based token budgeting, extractive compression, and source metadata.
- System configuration reports listener and durable-persistence status without exposing filesystem paths or database URLs.
- Generation rebuild/cutover is merged: bounded copy and reconciliation across Index, Documents, Vectors, Memory, and Knowledge; persistent write fencing; restart recovery; active-generation retrieval and writes; and authoritative Index freshness/tombstone filtering. PR #42 merged as `4c607ffd33f27f96b07d4fac58db20cd8b3afe95` with 204 passed and 1 skipped.

These are implemented slices, not a declaration that the platform is production-ready. Transcript parsing still relies on caller-supplied byte slices, while durable transcript jobs, byte checkpoints, and retry state are implemented. Automatic transcript-job startup recovery is intentionally deferred until those request-owned jobs have renewable leases; bulk recovery on every replica startup could interrupt work owned by another live instance. Server-side filesystem scanning remains intentionally out of scope without an explicit scoped-source contract.

## Remaining implementation sequence

1. Generation rebuild/cutover is merged on main.
2. Close remaining source lifecycle gaps: cleanup retry state, stale vector reconciliation, and failure recovery across Index, Documents, Knowledge, Graph, and Vector.
3. Complete Memory scoped-source reconciliation, event lifecycle semantics, conflict/deletion policy, and retrieval quality.
4. Add bounded deadlines/cancellation and durable operation tracking for long-running non-transcript ingestion and rebuild operations.
5. Finish Control Center usability and real execution diagnostics, while keeping all operations REST-backed.
6. Complete operational readiness: backup/restore, migrations/upgrade, observability, rate limits, authentication/authorization, and partition enforcement.
7. Verify the local embedding/reranking runtime matrix and decide when exact vector scans require a local ANN implementation.


## Pre-review completion policy (2026-11-06)

The repository's issues are part of the completion scope. No outside review is requested until feature parity is complete, all issues are implemented or explicitly resolved with a documented accepted scope decision, all acceptance criteria and tests are verified, and the outstanding PR queue is consolidated. SonarCloud/SonarQube is deliberately deferred until that implementation-completion point; it is not an active implementation gate.

Required validation for each capability:

- **Unit tests:** domain transitions, validation, deterministic algorithms, strategy/decorator composition, error mapping, and boundary conditions.
- **Persistence/integration tests:** SQLite migrations, transaction boundaries, reopen/restart durability, idempotent retries, partition isolation, and cross-store consistency.
- **REST integration tests:** request/response contracts, authorization and partition scoping, invalid transitions, error semantics, and Control Center API workflows where applicable.
- **Concurrency tests:** competing updates, duplicate dispatch, lease/heartbeat behavior, and race-sensitive lifecycle operations.
- **Failure-injection tests:** partial writes, interrupted operations, unavailable dependencies, rollback, retry, and recovery.
- **End-to-end tests:** complete ingestion, retrieval, deletion/reclamation, rebuild/cutover, and collaboration workflows rather than isolated route assertions.
- **Java/Python parity tests:** required only where both implementations exist. The current repository implementation is Python-only; do not invent a second runtime solely to satisfy a cross-language issue. Any language expansion requires an explicit implementation decision and matching contract tests.

The GitHub Actions workflow must expose clear validation stages and run the full suite. Tests must not be weakened, skipped, or reclassified merely to make CI green. Skips and warnings must be explained. After feature parity, issue closure, test completion, and PR consolidation, run SonarCloud and require its configured quality gate before outside review.

### Open issue completion order

1. #27 pluggable deletion lifecycle, including derived-data cleanup, retry/idempotency, and restore/purge boundaries.
2. #26 generic contract audit/refinement, applied where Python typing materially improves safety; REST remains language-neutral.
3. #43 collaboration and contributor identity contracts.
4. #44 attributable contributions and activity history.
5. #45 human/agent handoffs.
6. #46 project synthesis, competing proposals, and attributable decisions.

Issues #43–46 are no longer treated as review-exempt merely because their original descriptions call them post-parity. They must be completed before the final review gate. Their dependencies remain enforced: #43 before #44; #44 before #45 and #46.

An issue is complete only when implementation, documentation, tests, and acceptance criteria are all satisfied. Closing an issue because code exists or a route responds is not sufficient.


## PR #47 issue implementation update

The following implementation work is now present on the PR branch. It remains subject to full CI, SonarCloud, and final acceptance verification; these notes do not authorize closing issues prematurely.

- **#27 deletion lifecycle:** typed generic Strategy/Decorator/Result contracts; deferred and immediate queue strategies; persisted actor, reason, request time, and retention deadline; durable retention scheduling; append-only audit events for strategy outcomes and reclamation worker claims, deferrals, retries, cancellation, completion, and restoration; safe cancellation of pending reclamation; restoration of soft-deleted sources and retained Documents, Knowledge, and Vector generation materializations; fail-closed restoration after reclamation starts or completes; explicit legal-erasure override that bypasses future retention while retaining requested-deadline and override audit metadata; idempotent derived-data cleanup remains in place.
- **#26 generic contracts:** deletion contexts/results, strategy protocols, decorators, and reclamation jobs now preserve concrete Python types. Type-contract tests verify generic parameter preservation and typed queue results. The current repository has no Java implementation; no artificial Java source tree has been added. Java/Python equivalence remains an explicit limitation until a real Java implementation is introduced.
- **#43 identity and project contracts:** provider-neutral human/agent participants, projects, membership roles, validation, and language-neutral REST resources use the existing SQLite database.
- **#44 contributions and activity:** independently addressable attributable contributions, stable source references, project-scoped activity queries, and concurrent-write coverage are implemented.
- **#45 handoffs:** open/acknowledged/completed/superseded lifecycle, optimistic concurrency, append-only event history, incremental checkpoints, structured blockers/next actions/references, and supersession links are implemented. Environment-specific hooks remain outside Ensemble; integrations publish checkpoints through REST.
- **#46 synthesis and decisions:** replaceable deterministic synthesis strategy, contribution-linked statements, immutable refresh history, competing proposals, separate objections, decision history, contribution provenance, and human-gated final approval are implemented. Agent recommendations cannot accept or reject human-gated proposals.
- The Control Center now includes participant/project setup, membership, contributions, handoffs/checkpoints, synthesis, proposal, decision, and activity controls. The full collaboration contracts are documented in [collaboration-contracts.md](collaboration-contracts.md).

The new tests cover deletion metadata and retention persistence, audit history across restart, cancellation/restoration boundaries, generation materialization restoration, collaboration persistence across restart, concurrent contribution writes, concurrent handoff transitions, incremental checkpoints, synthesis refresh and strategy injection, proposal objections and provenance, human approval enforcement, and Control Center JavaScript syntax through the existing workflow. CI verification is still required before issue closure.


## PR #47 completion status

The implementation scope covered by PR #47 is now complete and all repository issues have been resolved:
- #26 closed as not planned: Python generic contracts are implemented and audited; no Java source/build target exists in this repository.
- #27 closed as completed: reversible deletion, pluggable reclamation, retention/legal-erasure policy, durable audit, retry/idempotency, restore boundaries, and derived-data cleanup.
- #43 closed as completed: provider-neutral identity, project, and membership contracts.
- #44 closed as completed: durable attributable contributions and activity history.
- #45 closed as completed: human/agent handoffs, checkpoints, lifecycle transitions, and Control Center workflows.
- #46 closed as completed: provenance-linked synthesis, competing proposals, objections, and human-gated decisions.

Latest verified code/test head: `29362779405a0890fc5d274bbadf77813522fd3c`, with 345 passed, 1 skipped, and 0 failures; the current PR Python workflow is successful. SonarCloud is intentionally deferred until feature parity, issue resolution, tests, and PR consolidation are complete. No outside reviewers are to be requested during implementation, and the PR must not be merged yet.

## Generic contract audit

See [generic-contract-audit.md](generic-contract-audit.md) for the implemented Python generic strategy/decorator contracts, the foundational store protocol audit, and the explicit Java-source boundary. No Java implementation is invented because this repository contains no Java source or build module.



## Issue #48 retrieval-scope audit update (2026-11-07)

Verified on branch `integration/source-lifecycle-mainline-2026-11-04`, commit `038c33368103e7f3043cf896f166bb3b32ae4c3f`:

- Hybrid Graph expansion is tested with exact entities and source/chunk candidates scoped to the authenticated partition.
- Hybrid reranking is tested with two authenticated partitions that intentionally reuse a source ID. Every reranker invocation receives only candidates from its request partition. The regression test also verifies final result text and per-channel diagnostics remain scoped when vector retrieval is deliberately unavailable.
- Hybrid reranking now occurs once, after Graph expansion. The inner retrieval pass explicitly disables reranking, avoiding duplicate scoring; the outer response preserves ranking status, provider, and candidate-count metadata.
- Knowledge hybrid vector retrieval is tested end to end through REST with the same source ID in two physically isolated SQLite partitions.
- Memory REST coverage verifies source imports, same event IDs with independent payloads, scope-bounded reconciliation, and semantic retrieval remain partition- and scope-local.
- Full CI at the verified code head: **356 passed, 1 skipped, 1 warning, 0 failed**. Installer shell syntax and Control Center JavaScript validation passed.

This completes the first retrieval-scope pass across lexical retrieval, vector-backed Knowledge retrieval, Graph expansion, final Hybrid results, per-channel diagnostics, and reranker inputs. The next audit pass should expand failure injection and continue the durable operation lifecycle work.

Memory retrieval evaluation remains limited to the explicitly labeled synthetic smoke fixture. No relevance judgments have been invented. Representative judged fixtures and measured Memory lexical/semantic/hybrid recall remain outstanding until genuine relevance labels are available.
