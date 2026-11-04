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
- Deterministic reciprocal-rank fusion with per-list contributions and tie-breaking.
- Optional local cross-encoder reranking provider with pinned model identity, bounded inputs, serialized scoring, fail-closed readiness, and opt-in integration into Hybrid retrieval with stable score alignment and graceful degraded fallback.
- SQLite FTS5 lexical indexing uses the shared lexical-v1 tokenizer, Porter stemming, compound identifier analysis, OR query matching, BM25 ranking, and transactional replacement with document revisions.
- Hybrid retrieval exposes independent Lexical and Hybrid REST groups, combines FTS5 BM25 and embedding candidates across the shared document/chunk index (including Knowledge and transcript ingestion), and reports independent lexical/vector channel status, availability, and candidate counts. A failed lexical channel no longer suppresses usable vector candidates; embedding-provider failure preserves lexical results; a vector-collection failure is distinguished from usable document-chunk vectors. Responses explicitly report ok, partial, or unavailable retrieval status and stable degraded dependency identifiers. Retrieval expands candidates through exact query-entity to source relationships in Graph and applies optional cross-encoder reranking after candidate fusion. It returns intent, entity, Graph-expansion, and related-session diagnostics.
- Hybrid prompt capture is stored in the SQLite Memory event ledger with deterministic event IDs and append-once idempotency. Bounded transcript-session ingestion stores message documents, FTS5 lexical entries, transcript_text Vector collection records when embeddings are available, append-once Memory event-ledger entries and Graph session/file relationships; repeated message events are idempotent.
- Vector similarity over stored chunk embeddings when embeddings are enabled.
- Document and chunk provenance in search responses.
- Tests for API behavior, migration setup, restart durability, and revision replacement.
- Python 3.12 GitHub Actions workflow validates installer shell syntax, Control Center JavaScript syntax, and the full Python test suite. The latest verified review-branch run passed 150 tests with 1 skipped.

Implemented on the review branch (latest verified CI: 150 passed, 1 skipped):
- POC-compatible lexical-v1 tokenization capability and REST endpoints.
- POC-compatible identifier extraction capability and REST endpoints.
- POC-compatible rule-based intent classification capability and REST endpoints.
- POC-compatible explainable reciprocal-rank fusion capability and REST endpoints.
- POC vector collection REST operations and durable SQLite collection/record storage; ANN indexing remains intentionally absent.
- POC graph node/edge mutation, deterministic identities, typed queries, bounded directional traversal, and SQLite persistence.
- SQLite-backed episodic Memory on the same REST listener: named memory read/write, append, append-once with payload-digest conflict detection, entries, list, lexical search, local semantic and hybrid search, context-aware recall, Markdown section chunking, idempotent Claude Code Markdown import with SHA-256 validation, source provenance, stale-source reconciliation, and knowledge status. Migrations `0006_memory` and `0009_memory_sources` persist documents, session events and imported source metadata.
- Standalone Thompson routing on the same REST listener: Beta(successes+1, failures+1) sampling, max-cost filtering, model outcome recording, state inspection and per-model reset. SQLite migrations `0008_thompson` and `0011_thompson_events` persist model statistics and idempotent outcome events, with atomic increments. The POC's `task_type` and `required_capability` inputs remain contract-compatible but are not used for filtering, matching the current POC implementation.
- Initial bounded GA capability on the same REST listener: five synthetic local evaluator families, parameter bounds, seeded reproducible population evolution, tournament selection, crossover, Gaussian mutation, elite preservation, fitness history, top candidates, persisted run status/results, and explicit approval-to-export records. Migration `0007_ga_runs` persists runs and approvals; candidate approval never applies live settings.
- Initial browser Control Center GA workflow: configure and launch bounded runs, inspect run history/results/fitness history/candidates, and explicitly approve a candidate for export without applying it to live settings.
- POC-compatible reranking REST endpoint and optional local cross-encoder provider (local model installation/runtime verification pending).
- Browser Control Center served by the same REST application at /control, with live capability health/readiness checks, hybrid retrieval and optional reranking, structured transcript-session ingestion, Index/Vector and Memory/Knowledge inspection, source lookup and confirmed tombstones, Entity extraction/resolution/linking, multi-intent classification, plus URI preview and explicit ingest.
- Bounded public-URI fetching, HTML/plain-text extraction, local/private destination rejection, redirect limits, response-size limits, and preview-only operation.
- Shared source lifecycle across supported document, transcript, PDF, URL, lexical, and Knowledge ingestion: Index revision decisions gate writes; accepted content enters the durable document/chunk index; Knowledge publication also writes provenance records and the `knowledge_chunks` vector collection when embeddings are available; exact extracted entities are associated with source revisions in Graph.

Not yet implemented:
- The remaining POC capability surface listed in [POC capability parity](poc-parity.md).
- Additional tokenization/analyzer profiles beyond lexical-v1.
- Dedicated approximate-nearest-neighbor vector index; exact dot-product search currently scans SQLite collection records.
- Remaining retrieval work: calibrated channel weighting and richer end-to-end trace provenance. Lexical search, query embedding, vector-collection lookup, and Graph expansion have bounded independent deadlines. Diagnostics include per-channel elapsed time and vector sub-stage timings for embedding, external lookup, and local document-vector scanning. Knowledge records now share the durable document/chunk index and participate in the same lexical/vector candidate fusion; Graph-linked exact entities can expand the candidate set. ANN indexing remains intentionally absent.
- Durable ingestion jobs and restart-safe transcript checkpoints remain incomplete. Context assembly is available through `POST /api/v1/context/assemble` and Memory recall. It uses a character-based token estimate and extractive sentence selection; exact model-tokenizer budgeting and model-backed summarization are not implemented. Current transcript JSONL parsing accepts caller-supplied slices and returns byte offsets, while structured session ingestion is idempotent but synchronous.
- Broader learned/model-backed entity extraction and fuzzy entity resolution; current extraction and resolution are deterministic and rule-based.
- Model-backed intent providers and calibrated confidence; current intent classification is deterministic and heuristic.
- Remaining POC Memory parity: direct scoped filesystem scanning and symlink-safe path traversal, full session-event compatibility and deeper matching against the POC's 48 memory tests. Current semantic search is deterministic local term-vector cosine, not an embedding-backed ANN index; the source-import endpoint accepts content and verified path metadata rather than opening arbitrary paths. Initial Learning capability is implemented on the same REST listener: task outcome validation, payload-digest idempotency, SQLite checkpoint/status, Memory event publication, idempotent Thompson outcome updates, Thompson state snapshots, report/recent-outcome APIs, portable artifact persistence and explicit reset. Migrations `0010_learning` and `0011_thompson_events` support the workflow. Remaining Learning parity includes a deeper POC test/edge-case audit and any future client/circuit-breaker behavior outside the co-located service boundary. Scrape-ledger remains unimplemented. GA Tuning has an initial optimizer/API/persistence slice, but full POC evaluator parity, scheduled execution/recovery, portable learning artifacts, and Control Center configuration/progress/candidate comparison are not complete. The GA's Thompson evaluator is not the standalone Thompson service.
- Remaining Control Center work: model/provider configuration, independent retrieval-channel comparison, general background-job history, operational metrics, correlated diagnostics, and richer maintenance controls. The current UI includes capability, retrieval, Context Assembly, Index/Vector, Memory/Knowledge, Graph, Entity, Chunking, Tokenization, pipeline diagnostics, transcript ingestion, URI ingestion, GA, Thompson and Learning workbenches.
- Multi-page bounded crawling, robots.txt policy, DNS-to-connection pinning, retry/recovery orchestration, and scheduled source refresh.
- Authentication, partition enforcement, durable ingestion/background jobs, production observability, rate limits, backup/restore automation, and deployment/upgrade hardening.

SQLite is the only persistence implementation in this repository. FTS5 provides the lexical index. Embeddings are stored as JSON and scored by exact scan in the application; this is not a scalable approximate-nearest-neighbor vector index.


## Index generation coordination foundation

Implemented:
- Versioned SQLite migration for durable index-generation metadata and a singleton active-generation pointer.
- Monotonically increasing active-generation epoch for compare-and-swap cutover.
- Database constraint allowing at most one generation in the building state.
- Independent sealed checkpoints for Index, Memory, and Knowledge.
- Cutover guard requiring all three checkpoints sealed plus explicit acknowledged-write and tombstone verification.
- Atomic active-pointer update and active/retired state transition in one SQLite transaction.
- In-memory adapter with matching lifecycle and cutover rules.

Not yet implemented:
- Shadow generation storage for actual document/chunk/vector retrieval data.
- Write fences coordinated with every ingestion and tombstone writer.
- Reconciliation of writes accepted while the generation is being built.
- Restart recovery for interrupted rebuilds and uncertain cutover.
- Public rebuild operations. These remain intentionally unavailable until the data-plane guarantees are implemented.


## Shadow vector generation storage

Implemented:
- Versioned SQLite migration 0017 for generation-scoped vector records, keyed by collection, generation, and record ID.
- Durable shadow-vector upsert, query, and generation cleanup operations.
- In-memory adapter with matching generation isolation.
- Shadow writes and queries do not alter or leak into the existing active vector collection query path.
- Tests cover same-record-ID isolation between active and shadow generations and cleanup without disturbing active records.

Still outstanding:
- Shadow document/chunk and lexical storage.
- Rebuild worker and source snapshot/cursor handling.
- Write-fence coordination and reconciliation of writes accepted during rebuild.
- End-to-end active generation cutover and crash recovery.


## Shadow document and chunk generation storage

Implemented:
- Versioned SQLite migration 0018 for generation-scoped document and chunk records.
- Stable document identity within a generation and source/revision uniqueness.
- Newest-revision selection is scoped to the shadow generation and does not alter active document state.
- Generation-scoped chunk enumeration and lexical candidate search.
- Shadow generation cleanup removes its document and chunk records without affecting active documents.
- In-memory adapter mirrors generation isolation and revision selection.
- Tests cover identical source/revision values in active and shadow data, newer revision selection, lexical lookup, and isolated cleanup.

Still outstanding:
- Tombstone/source-state propagation into shadow generations.
- A durable rebuild worker that snapshots and populates document, lexical, and vector stores.
- Write-fence coordination, concurrent-write reconciliation, and crash recovery.
- Atomic activation across all retrieval data planes.
