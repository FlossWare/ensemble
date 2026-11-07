# Implementation status

## Implementation decision

Ensemble is implemented in Python. This is an Ensemble-specific choice and does not establish a language standard for the wider FlossWare ecosystem. REST contracts remain language-neutral.

## Durable persistence milestone

Implemented:
- SQLAlchemy 2 asynchronous persistence adapter.
- SQLite is the only persistence implementation in this repository; the default durable store is .ensemble/ensemble.db.
- Alembic-managed, versioned schema migrations; application startup rejects an unmigrated or incomplete database.
- Document and chunk records are stored transactionally.
- Stable document identity for repeated ingestion of the same source and revision.
- Stable chunk identities for a given document revision and ordinal.
- Re-ingestion replaces the indexed chunk set atomically rather than accumulating duplicate chunks.
- Revisions are retained; search returns chunks from the highest revision for each source.
- An older revision arriving late cannot replace a newer revision in the active search view.
- Persistence survives application restart.
- Storage details remain behind the document-store interface and do not appear in REST requests or responses.

PostgreSQL and other alternative persistence implementations are not part of Ensemble's scope. FlossWare proper owns shared infrastructure and alternative persistence implementations.

## First vertical slice

Implemented:
- FastAPI application with generated OpenAPI documentation at /docs.
- Durable SQLite document and chunk storage.
- Versioned window-v1, markdown-v1, and message-v1 profiles are integrated into document ingestion; selected profile, exact offsets, and Markdown heading provenance persist in SQLite and are returned by search.
- Standalone versioned window-v1, markdown-v1, and message-v1 chunk profiles with stable IDs, source offsets, and heading provenance.
- Optional OpenAI-compatible embedding provider.
- Versioned lexical-v1 tokenization with deterministic compound identifier handling and source offsets.
- Structured transcript metadata (session ID, project, role, message kind, subagent flag and source file) persists with document revisions and is returned by Lexical search; identical message text is deduplicated in Lexical results.
- Entity extraction with typed candidates, normalized values, stable candidate IDs, exact/uncertain confidence, source offsets, and optional provenance.
- Graph-backed canonical entity resolution with deterministic identities, exact normalized matching, explicit ambiguity results, and idempotent creation.
- Entity linking through typed Graph edges, restricted to canonical entity nodes.
- Explainable multi-intent rule-based classification with primary and secondary intents, evidence spans, heuristic confidence, extracted constraints, and required-capability hints.
- Reusable provider-neutral weighted reciprocal-rank fusion exposed through `/api/v1/ranking/fuse`, with configurable k, stable identity deduplication, deterministic ties, per-channel ranks/contributions, and static channel weights. Hybrid retrieval applies the same RRF implementation across lexical, vector, and Graph-expanded candidates before optional reranking. An opt-in Thompson policy samples weights from persisted `retrieval:<channel>` outcomes; callers submit idempotent judgments through `/api/v1/search/feedback`. It remains disabled by default and falls back to static weights until feedback exists. Pipeline diagnostics expose the selected strategy, weights, ranks, and per-channel contributions. `ensemble.retrieval_eval` and `scripts/evaluate-retrieval.py` provide offline Recall@k, MRR@k, and nDCG@k comparisons over caller-supplied qrels and ranked runs.
- Optional ranking-provider contract and reusable async reranking decorator; the pinned local cross-encoder is adapted behind it, and an explicitly configured HTTP provider supports bounded request/response payloads, strict score validation, authorization headers, timeouts, and graceful baseline fallback. RRF remains the deterministic default.
- SQLite FTS5 lexical indexing uses the shared lexical-v1 tokenizer, Porter stemming, compound identifier analysis, OR query matching, BM25 ranking, and transactional replacement with document revisions.
- Hybrid retrieval exposes independent Lexical and Hybrid REST groups, combines FTS5 BM25 and embedding candidates across the shared document/chunk index (including Knowledge and transcript ingestion), and reports independent lexical/vector channel status, availability, candidate counts, and bounded ranked candidate lists with source/revision/chunk provenance and channel scores (up to 100 per channel). A failed lexical channel no longer suppresses usable vector candidates; embedding-provider failure preserves lexical results; a vector-collection failure is distinguished from usable document-chunk vectors. Responses explicitly report ok, partial, or unavailable retrieval status and stable degraded dependency identifiers. Retrieval expands candidates through exact query-entity to source relationships in Graph and applies optional cross-encoder reranking after candidate fusion. It returns intent, entity, Graph-expansion, and related-session diagnostics.
- Hybrid prompt capture is stored in the SQLite Memory event ledger with deterministic event IDs and append-once idempotency. Bounded transcript-session ingestion stores message documents, FTS5 lexical entries, transcript_text Vector collection records when embeddings are available, append-once Memory event-ledger entries and Graph session/file relationships; repeated message events are idempotent.
- Vector similarity over stored chunk embeddings when embeddings are enabled.
- Document and chunk provenance in search responses.
- Tests for API behavior, migration setup, restart durability, and revision replacement.
- Python 3.12 GitHub Actions workflow validates installer shell syntax, Control Center JavaScript syntax, and the full Python test suite. The latest verified PR workflow passed the full suite and both syntax validations.

Implemented on the review branch (latest verified PR workflow: green):
- POC-compatible lexical-v1 tokenization capability and REST endpoints.
- POC-compatible identifier extraction capability and REST endpoints.
- POC-compatible rule-based intent classification capability and REST endpoints.
- POC-compatible explainable reciprocal-rank fusion capability and REST endpoints.
- POC vector collection REST operations and durable SQLite collection/record storage; ANN indexing remains intentionally absent.
- POC graph node/edge mutation, deterministic identities, typed queries, bounded directional traversal, and SQLite persistence.
- SQLite-backed episodic Memory on the same REST listener: named memory read/write/delete, append, append-once with payload-digest conflict detection, timezone-aware event expiry (expired events are excluded from recall but retained for audit), entries, list, lexical search, local semantic and hybrid search, context-aware recall, Markdown section chunking, idempotent Claude Code Markdown import with SHA-256 validation, source provenance, stale-source reconciliation, and knowledge status. Migrations `0006_memory` and `0009_memory_sources` persist documents, session events and imported source metadata.
- Standalone Thompson routing on the same REST listener: Beta(successes+1, failures+1) sampling, max-cost filtering, model outcome recording, state inspection and per-model reset. SQLite migrations `0008_thompson` and `0011_thompson_events` persist model statistics and idempotent outcome events, with atomic increments. The POC's `task_type` and `required_capability` inputs remain contract-compatible but are not used for filtering, matching the current POC implementation.
- Initial bounded GA capability on the same REST listener: five synthetic local evaluator families, parameter bounds, seeded reproducible population evolution, tournament selection, crossover, Gaussian mutation, elite preservation, fitness history, top candidates, persisted run status/results, and explicit approval-to-export records. Migration `0007_ga_runs` persists runs and approvals; candidate approval never applies live settings.
- Initial browser Control Center GA workflow: configure and launch bounded runs, inspect run history/results/fitness history/candidates, and explicitly approve a candidate for export without applying it to live settings.
- POC-compatible reranking REST endpoint, local cross-encoder adapter, and optional HTTP ranking-provider adapter behind a reusable decorator (local model installation/runtime verification pending).
- Browser Control Center served by the same REST application at /control, with live capability health/readiness checks, hybrid retrieval with independent lexical/vector candidate lists and RRF provenance, dedicated Embeddings and Ranking/Reranking provider diagnostics and bounded probes, structured transcript-session ingestion, Index/Vector and Memory/Knowledge inspection, source lookup and confirmed tombstones, read-only durable Source Reclamation queue/per-source audit inspection, durable Index Generation lifecycle workbench (status, bounded copy/reconcile, confirmed fence/activate/abort), Entity extraction/resolution/linking, multi-intent classification, URI preview and explicit ingest, plus a Pipeline Inspector with non-persistent diagnostics and persisted execution-trace list/detail.
- Bounded public-URI fetching, HTML/plain-text extraction, local/private destination rejection, redirect limits, response-size limits, and preview-only operation.
- Shared source lifecycle across supported document, transcript, PDF, URL, lexical, and Knowledge ingestion: Index revision decisions gate writes; accepted content enters the durable document/chunk index; Knowledge publication also writes provenance records and the `knowledge_chunks` vector collection when embeddings are available; exact extracted entities are associated with source revisions in Graph.
- Index tombstones use one cleanup path across every vector collection, and Knowledge-only vector retrieval revalidates source revision and tombstone state against Index before returning candidates.

Not yet implemented:
- The remaining POC capability surface listed in [POC capability parity](poc-parity.md).
- Additional tokenization/analyzer profiles beyond lexical-v1.
- Dedicated approximate-nearest-neighbor vector index; exact dot-product search currently scans SQLite collection records.
- Remaining retrieval work: calibrated channel weighting and richer end-to-end trace provenance. Lexical search, query embedding, vector-collection lookup, and Graph expansion have bounded independent deadlines. Diagnostics include per-channel elapsed time and vector sub-stage timings for embedding, external lookup, and local document-vector scanning. Knowledge records now share the durable document/chunk index and participate in the same lexical/vector candidate fusion; Graph-linked exact entities can expand the candidate set. ANN indexing remains intentionally absent.
- Retrieval evaluation now provides Recall@k, MRR@k, and nDCG@k comparisons across lexical, vector, unweighted RRF, weighted RRF, and Thompson-adaptive runs. The evaluator rejects duplicate candidate identities and run queries without relevance judgments. Representative judged qrels are still required before drawing quality conclusions or enabling Thompson-adaptive weighting by default. The current harness accepts caller-supplied JSON qrels/runs; it does not manufacture relevance labels.
- Durable transcript ingestion jobs use persisted byte checkpoints and renewable five-minute owner leases. Each message renews the lease; duplicate dispatch cannot claim a live slice; completion/failure is conditional on the current owner; startup recovery marks only expired leases interrupted, so another replica cannot interrupt live work. Migration `0038_transcript_job_leases` gives pre-lease running jobs a fifteen-minute grace period. The parser still accepts caller-supplied byte slices rather than opening arbitrary server-side paths. The durable-operation audit found no other detached ingestion workers in this branch: document, PDF, URL, and Knowledge routes are request-scoped; crawl/recurring-source work uses durable leased jobs; and Index generation rebuilds use durable generation records, per-capability checkpoints, resumable cursors, explicit abort, and startup write-fence recovery. The Control Center exposes the generation lifecycle as bounded copy/reconcile operations plus confirmed fence, activation, and abort. Any future detached ingestion adapter must implement durable ownership, bounded status, retry/cancellation, and restart recovery before it is enabled. Context assembly is available through `POST /api/v1/context/assemble` and Memory recall. The default character/4 token estimate is explicitly approximate; an optional configured Hugging Face tokenizer provides exact model-token counts and token-aware extractive compression. Model-backed summarization is not implemented.
- Broader learned/model-backed entity extraction and fuzzy entity resolution; current extraction and resolution are deterministic and rule-based.
- Model-backed intent providers and calibrated confidence; current intent classification is deterministic and heuristic.
- Remaining Memory work: broader session-event compatibility and retrieval-quality evaluation. Scoped source import deliberately uses caller-supplied content plus canonical path/scope metadata and SHA-256 verification; the service does not open arbitrary client filesystem paths. Symlink and scope-escape aliases are rejected. Semantic search is deterministic local term-vector cosine, not an embedding-backed ANN index. Initial Learning capability is implemented on the same REST listener: task outcome validation, payload-digest idempotency, SQLite checkpoint/status, Memory event publication, idempotent Thompson outcome updates, Thompson state snapshots, report/recent-outcome APIs, portable artifact persistence and explicit reset. Migrations `0010_learning` and `0011_thompson_events` support the workflow. Remaining Learning parity includes a deeper POC test/edge-case audit and any future client/circuit-breaker behavior outside the co-located service boundary. Scrape-ledger remains unimplemented. GA Tuning has an initial optimizer/API/persistence slice, but full POC evaluator parity, scheduled execution/recovery, portable learning artifacts, and Control Center configuration/progress/candidate comparison are not complete. The GA's Thompson evaluator is not the standalone Thompson service.
- Remaining Control Center work: bounded operational metrics, general durable-operation history where backend contracts support it, correlated diagnostics, safe migration/backup/restore, and richer maintenance controls. The current UI includes capability, retrieval with independent channel comparison, Embeddings and Ranking/Reranking diagnostics, Context Assembly, Index/Vector and Index Generation lifecycle, Memory/Knowledge, Graph, Entity, Chunking, Tokenization, pipeline diagnostics, transcript ingestion, URI ingestion, GA, Thompson and Learning workbenches.
- Bounded same-origin crawl preview is available through `POST /api/v1/ingest/urls/crawl-preview`. It applies robots.txt rules (including crawl-delay), pins each HTTP connection to the DNS address that passed public-address validation, blocks cross-origin redirects, and enforces page-attempt, depth, response-size, and total-character bounds. Durable crawl jobs are available through `POST /api/v1/ingest/urls/crawl-jobs`, with status/list/retry endpoints, SQLite persistence, a background worker, startup recovery of interrupted jobs, and Control Center queue/status/retry controls. Completed jobs persist crawl preview and per-page results. `POST /api/v1/ingest/urls/crawl-jobs/{job_id}/ingest` ingests persisted pages with per-page durable checkpoints: a revision is recorded before writes, completed pages are skipped on replay, and failed/interrupted pages retry using the same revision. Recurring source schedules are managed through `/api/v1/ingest/urls/schedules`; the scheduler atomically dispatches crawl jobs configured for automatic ingestion. Schedules can be paused, resumed, and deleted. Partitioned deployments must configure `ENSEMBLE_INTERNAL_BEARER_TOKEN` as a service principal authorized for each scheduled partition.
- Authentication, partition enforcement, durable ingestion/background jobs, production observability, rate limits, backup/restore automation, and deployment/upgrade hardening.

SQLite is the only persistence implementation in this repository. FTS5 provides the lexical index. Embeddings are stored as JSON and scored by exact scan in the application; this is not a scalable approximate-nearest-neighbor vector index.

## Generation rebuild and cutover (merged on main)

[PR #42](https://github.com/FlossWare/ensemble/pull/42) merged as `4c607ffd33f27f96b07d4fac58db20cd8b3afe95`.

- Durable generation-scoped Memory snapshots and a mutation journal, including writes, append-once entries, deletes, imported Markdown metadata, and stale-source reconciliation.
- Bounded REST copy and reconciliation operations for Index, Documents, Vectors, Memory, and Knowledge.
- A shared write fence enforced inside Index, Documents, Vectors, Knowledge, and Memory write transactions; startup recovery releases an abandoned fence and reopens all reconciliation checkpoints.
- Active-generation retrieval for lexical and vector candidates, with FTS5/BM25 scoring retained.
- Post-activation document/vector/Knowledge writes, Index manifest changes, Memory snapshots, and tombstones are reflected in the active generation.
- Activation is retry-safe when the original response is lost; retrieval checks authoritative Index revision and tombstone state.
- Merge CI passed **204 tests, 1 skipped**.

## Source lifecycle integration branch

Branch `integration/source-lifecycle-mainline-2026-11-04` is in progress and not yet merged.

- Base and generation search both validate every candidate against current Index revision, tombstone, and permanent-purge state.
- Graph and Knowledge retrieval also exclude permanently purged sources.
- Index tombstones enqueue durable reclamation jobs; partial immediate cleanup does not prevent queueing.
- The reclamation worker removes active materialized data and marks the Index source permanently purged only after all stores report completion. Purge marking is retry-safe.
- `GET /api/v1/index/reclamation` reports queue counts; `POST /api/v1/index/reclamation/run` triggers a bounded pass.
- Document publication and source tombstones are required to follow Index lifecycle state. Untracked standalone vector mutations are rejected while a generation is building.
- Generation recovery fails closed for missing or unexpected generation states. Application startup releases an abandoned fence, invalidates reconciliation checkpoints, and remains idempotent on repeated startup.
- Activation rejects incomplete checkpoints. Injected failure during the activation transaction preserves the previous active pointer, generation state, epoch, and write fence.
- Memory generation snapshots reconcile updates and deletions during rebuild and continue mirroring writes and deletes after activation.
- Vector generation mirroring reflects the persisted base Vector state. A failed active-generation mirror leaves the Index journal unacknowledged for retry; non-embedded revisions remove stale vectors from both stores.
- The source-lifecycle integration snapshot previously passed **271 tests, 1 skipped, 1 warning**. The latest PR workflow has since passed the full suite after the partition-aware crawl recovery and robots crawl-delay fixes.

## Partition isolation implementation

Implemented in the current partition-isolation branch:

- The existing configured SQLite database remains the `default` partition; named partitions are physically isolated in separate SQLite files under the persistence root's `partitions` directory.
- Every SQLite-backed capability store resolves its connection from the authenticated request scope: Documents/Chunks, Vector, Index, Knowledge, Graph, Memory, transcript checkpoints, GA, Thompson, Learning, and generation metadata.
- New named partition databases run the repository's Alembic migration chain on first use and record a partition identity marker. Existing default-partition data is not copied into named partitions.
- Bearer credentials are matched by SHA-256 digest against `ENSEMBLE_PARTITION_TOKENS`. The token's partition allow-list is authoritative; `X-Ensemble-Partition` is only a selector.
- Unauthenticated local mode is restricted to the default partition. Non-loopback listeners require a configured partition token map.
- Internal REST calls propagate the authenticated partition and credential. The Control Center now has tab-scoped credential and partition fields.

Verification on the latest successful partition CI run: **166 passed, 1 skipped**. This includes end-to-end ingestion/retrieval isolation, Memory/Knowledge/Graph/Vector separation, token allow-list checks, physical database separation for duplicate source IDs, remote-request rejection in local mode, and non-loopback listener authentication configuration.

Still required before partition isolation is review-ready: broader adversarial coverage across all capability types and destructive operations; backup/restore verification for the complete partition directory; key rotation/revocation and credential administration; rate limiting and audit/diagnostic coverage; and a security review of the Control Center and remote capability trust boundary. In-memory adapters remain test-only and are rejected when partition credentials are configured.


## PR #47 current branch additions (implementation and CI verified)

- Deletion lifecycle persists actor, reason, request time, requested retention deadline, and explicit legal-erasure override metadata. Future reclamation is durably scheduled across restarts; legally required erasure bypasses retention and cannot be restored.
- The deletion audit ledger records strategy results and worker lifecycle events. Soft-deleted sources can be restored only while reclamation remains pending/retryable; running or completed reclamation blocks restoration. Documents, Knowledge, Vector, and retained generation materializations are restored until physical purge.
- SQLite reclamation claims are atomic across workers. Permanent purge removes Document, Knowledge, Vector, and source-specific Graph materializations before marking the Index source purged; shared Graph entities remain.
- Python deletion contracts use generic strategy/result/decorator protocols and a typed reclamation job record. The generic contract audit is documented in [generic-contract-audit.md](generic-contract-audit.md). The repository remains Python-only; no Java implementation is fabricated.
- Collaboration REST resources cover human/agent identity, project membership, attributable contributions, activity history, incremental handoffs/checkpoints, provenance-linked synthesis, competing proposals, objections, and human-gated decisions. SQLite is reused; no service or database was added.
- The Control Center includes a collaboration workbench with activity refresh, contribution review, handoff create/checkpoint/acknowledge/complete/supersede, synthesis, proposal, and decision workflows. Integrations remain responsible for environment-specific hook capture and publish structured checkpoints through REST.
- All repository issues in this PR's completion scope are resolved: #26 closed as not planned because no Java implementation exists; #27 and #43–#46 closed as completed after implementation and acceptance-test coverage.
- Latest verified PR head `849175c4fcaca535659b42404af6ce54424be6d4`: **345 passed, 1 skipped**, installer shell syntax passed, Control Center JavaScript syntax passed, and GitGuardian passed with 250 commits scanned and no secrets detected.
- SonarCloud remains the sole known validation blocker: its new-code Reliability and Security ratings are C (both require A). The SonarCloud issue detail page is inaccessible through the available GitHub/web connections, so the individual findings still need to be obtained and fixed before the quality gate can pass.
