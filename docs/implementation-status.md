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
- Hybrid retrieval exposes independent Lexical and Hybrid REST groups, combines FTS5 BM25 and embedding candidates across the shared document/chunk index (including Knowledge and transcript ingestion), preserves lexical results with explicit degraded dependency reporting when embeddings or Vector writes fail, expands candidates through exact query-entity to source relationships in Graph, and applies optional cross-encoder reranking after candidate fusion. It returns intent, entity, Graph-expansion, and related-session diagnostics.
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
- Remaining retrieval work: external ranking-service fallback, calibrated channel weighting, bounded request deadlines/cancellation, and more complete retrieval tracing. Knowledge records now share the durable document/chunk index and participate in the same lexical/vector candidate fusion; Graph-linked exact entities can expand the candidate set. ANN indexing remains intentionally absent.
- Durable ingestion jobs and restart-safe transcript checkpoints remain incomplete. Context assembly is available through `POST /api/v1/context/assemble` and Memory recall. It uses a character-based token estimate and extractive sentence selection; exact model-tokenizer budgeting and model-backed summarization are not implemented. Current transcript JSONL parsing accepts caller-supplied slices and returns byte offsets, while structured session ingestion is idempotent but synchronous.
- Broader learned/model-backed entity extraction and fuzzy entity resolution; current extraction and resolution are deterministic and rule-based.
- Model-backed intent providers and calibrated confidence; current intent classification is deterministic and heuristic.
- Remaining POC Memory parity: direct scoped filesystem scanning and symlink-safe path traversal, full session-event compatibility and deeper matching against the POC's 48 memory tests. Current semantic search is deterministic local term-vector cosine, not an embedding-backed ANN index; the source-import endpoint accepts content and verified path metadata rather than opening arbitrary paths. Initial Learning capability is implemented on the same REST listener: task outcome validation, payload-digest idempotency, SQLite checkpoint/status, Memory event publication, idempotent Thompson outcome updates, Thompson state snapshots, report/recent-outcome APIs, portable artifact persistence and explicit reset. Migrations `0010_learning` and `0011_thompson_events` support the workflow. Remaining Learning parity includes a deeper POC test/edge-case audit and any future client/circuit-breaker behavior outside the co-located service boundary. Scrape-ledger remains unimplemented. GA Tuning has an initial optimizer/API/persistence slice, but full POC evaluator parity, scheduled execution/recovery, portable learning artifacts, and Control Center configuration/progress/candidate comparison are not complete. The GA's Thompson evaluator is not the standalone Thompson service.
- Remaining Control Center work: model/provider configuration, independent retrieval-channel comparison, general background-job history, operational metrics, correlated diagnostics, and richer maintenance controls. The current UI includes capability, retrieval, Context Assembly, Index/Vector, Memory/Knowledge, Graph, Entity, Chunking, Tokenization, pipeline diagnostics, transcript ingestion, URI ingestion, GA, Thompson and Learning workbenches.
- Multi-page bounded crawling, robots.txt policy, DNS-to-connection pinning, retry/recovery orchestration, and scheduled source refresh.
- Authentication, partition enforcement, durable ingestion/background jobs, production observability, rate limits, backup/restore automation, and deployment/upgrade hardening.

SQLite is the only persistence implementation in this repository. FTS5 provides the lexical index. Embeddings are stored as JSON and scored by exact scan in the application; this is not a scalable approximate-nearest-neighbor vector index.

## Partition isolation implementation

Implemented in the current partition-isolation branch:

- The existing configured SQLite database remains the `default` partition; named partitions are physically isolated in separate SQLite files under the persistence root's `partitions` directory.
- Every SQLite-backed capability store resolves its connection from the authenticated request scope: Documents/Chunks, Vector, Index, Knowledge, Graph, Memory, transcript checkpoints, GA, Thompson, Learning, and generation metadata.
- New named partition databases run the repository's Alembic migration chain on first use and record a partition identity marker. Existing default-partition data is not copied into named partitions.
- Bearer credentials are matched by SHA-256 digest against `ENSEMBLE_PARTITION_TOKENS`. The token's partition allow-list is authoritative; `X-Ensemble-Partition` is only a selector.
- Unauthenticated local mode is restricted to the default partition. Non-loopback listeners require a configured partition token map.
- Internal REST calls propagate the authenticated partition and credential. The Control Center now has tab-scoped credential and partition fields.

Verification on the latest successful partition CI run: **164 passed, 1 skipped**. This includes the end-to-end ingestion/retrieval isolation test, token allow-list checks, physical database separation for duplicate source IDs, and non-loopback listener authentication configuration.

Still required before partition isolation is review-ready: broader adversarial coverage across all capability types and destructive operations; backup/restore verification for the complete partition directory; key rotation/revocation and credential administration; rate limiting and audit/diagnostic coverage; and a security review of the Control Center and remote capability trust boundary. In-memory adapters remain test-only and are rejected when partition credentials are configured.
