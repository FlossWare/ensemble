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
- Deterministic character-window chunking in the initial document-ingestion path.
- Standalone versioned window-v1, markdown-v1, and message-v1 chunk profiles with stable IDs, source offsets, and heading provenance.
- Optional OpenAI-compatible embedding provider.
- Versioned lexical-v1 tokenization with deterministic compound identifier handling and source offsets.
- Exact identifier/entity extraction with uncertainty and confidence metadata.
- Explainable rule-based intent classification with evidence and per-label scores.
- Deterministic reciprocal-rank fusion with per-list contributions and tie-breaking.
- Optional local cross-encoder reranking provider with pinned model identity, bounded inputs, serialized scoring, and fail-closed readiness.
- Lexical retrieval and reciprocal-rank fusion.
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
- Initial SQLite-backed episodic Memory capability on the same REST listener: named memory read/write, append, append-once with payload-digest conflict detection, entries, list, lexical keyword search, context-aware recall, and deterministic chunking. Migration `0006_memory` persists memory documents and session-event records.
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
- Full POC hybrid-search orchestration and degraded-dependency behavior.
- Generalize the Index-to-Knowledge publication pipeline to all document ingestion paths; the URI scraper currently uses it.
- Broader learned/entity extraction beyond the POC's rule-based identifier extraction.
- Any future model-backed intent providers, which are not part of the current POC behavior.
- Remaining POC Memory parity: semantic and hybrid search, embedding-backed memory indexing, Markdown ingestion and reconciliation, knowledge status, search-index warm-up/readiness semantics, and full session-event compatibility. Learning, Thompson, GA Tuning, and scrape-ledger remain unimplemented. GA Tuning is explicitly in parity scope; its optimizer, five evaluator families, parameter/result schemas, scheduled execution, artifacts, and dashboard/control workflows are not implemented yet.
- Complete Control Center beyond the initial URI-ingestion workbench: service/model configuration, retrieval explorer, document/index management, job history, metrics, and maintenance controls.
- Multi-page bounded crawling, robots.txt policy, DNS-to-connection pinning, durable ingestion jobs, retries, and scheduled source refresh.
- Authentication, partition enforcement, ingestion connectors/background jobs, production observability, rate limits, and deployment packaging.

SQLite is the only persistence implementation in this repository. Embeddings are currently stored as JSON and scored in the application; this is not a scalable vector index.
