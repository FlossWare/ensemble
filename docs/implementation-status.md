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
- Exact identifier/entity extraction with uncertainty and confidence metadata.
- Explainable rule-based intent classification with evidence and per-label scores.
- Deterministic reciprocal-rank fusion with per-list contributions and tie-breaking.
- Optional local cross-encoder reranking provider with pinned model identity, bounded inputs, serialized scoring, fail-closed readiness, and opt-in integration into Hybrid retrieval with stable score alignment and graceful degraded fallback.
- SQLite FTS5 lexical indexing uses the shared lexical-v1 tokenizer, Porter stemming, compound identifier analysis, OR query matching, BM25 ranking, and transactional replacement with document revisions.\n- Hybrid retrieval exposes independent Lexical and Hybrid REST groups, combines lexical and vector rankings, preserves lexical results with explicit degraded dependency reporting when embeddings fail, optionally applies cross-encoder reranking, enriches results with rule-based intent, and finds related sessions through exact file-path matches in Graph.\n- Hybrid prompt capture is stored in the SQLite Memory event ledger with deterministic event IDs and append-once idempotency; it explicitly reports that prompt capture alone does not index transcript content.
- Vector similarity over stored chunk embeddings when embeddings are enabled.
- Document and chunk provenance in search responses.
- Tests for API behavior, migration setup, restart durability, and revision replacement.
- Python 3.12 GitHub Actions test workflow.

Implemented on the parity branch (GitHub Actions has verified the latest completed test run):
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
- Browser Control Center served by the same REST application at /control, with a URI preview and explicit ingest workflow.
- Bounded public-URI fetching, HTML/plain-text extraction, local/private destination rejection, redirect limits, response-size limits, and preview-only operation.
- Scraped-page ingestion through Index revision decisions, document/chunk persistence, Knowledge provenance, and SQLite vector collections when embeddings are available.

Not yet implemented:
- The remaining POC capability surface listed in [POC capability parity](poc-parity.md).
- Integration of the new versioned chunk profiles into document ingestion and persisted chunk provenance.
- Additional tokenization/analyzer profiles beyond lexical-v1.
- Dedicated approximate-nearest-neighbor vector index; exact dot-product search currently scans SQLite collection records.
- Integration of reranking into the hybrid retrieval pipeline.
- Full POC hybrid-search orchestration: independent knowledge/transcript channels, related-session graph lookup, intent enrichment, ranking-service fallback, optional cross-encoder reranking, and transcript/session ingestion are not yet complete. Current Hybrid endpoint has lexical/vector retrieval, embedding-degradation fallback, optional cross-encoder reranking, intent classification and exact-path related-session lookup; knowledge-only search is exposed separately.
- Generalize the Index-to-Knowledge publication pipeline to all document ingestion paths; the URI scraper currently uses it.
- Broader learned/entity extraction beyond the POC's rule-based identifier extraction.
- Any future model-backed intent providers, which are not part of the current POC behavior.
- Remaining POC Memory parity: direct scoped filesystem scanning and symlink-safe path traversal, full session-event compatibility and deeper matching against the POC's 48 memory tests. Current semantic search is deterministic local term-vector cosine, not an embedding-backed ANN index; the source-import endpoint accepts content and verified path metadata rather than opening arbitrary paths. Initial Learning capability is implemented on the same REST listener: task outcome validation, payload-digest idempotency, SQLite checkpoint/status, Memory event publication, idempotent Thompson outcome updates, Thompson state snapshots, report/recent-outcome APIs, portable artifact persistence and explicit reset. Migrations `0010_learning` and `0011_thompson_events` support the workflow. Remaining Learning parity includes a deeper POC test/edge-case audit and any future client/circuit-breaker behavior outside the co-located service boundary. Scrape-ledger remains unimplemented. GA Tuning has an initial optimizer/API/persistence slice, but full POC evaluator parity, scheduled execution/recovery, portable learning artifacts, and Control Center configuration/progress/candidate comparison are not complete. The GA's Thompson evaluator is not the standalone Thompson service.
- Complete Control Center beyond the initial URI-ingestion, GA, Thompson and Learning workbenches: capability explorer, retrieval explorer, document/index management, Memory/Knowledge inspection, model/provider configuration, general job history, metrics, diagnostics, and maintenance controls.
- Multi-page bounded crawling, robots.txt policy, DNS-to-connection pinning, durable ingestion jobs, retries, and scheduled source refresh.
- Authentication, partition enforcement, ingestion connectors/background jobs, production observability, rate limits, and deployment packaging.

SQLite is the only persistence implementation in this repository. FTS5 provides the lexical index. Embeddings are stored as JSON and scored by exact scan in the application; this is not a scalable approximate-nearest-neighbor vector index.
