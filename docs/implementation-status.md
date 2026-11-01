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
- Deterministic character-window chunking with overlap.
- Optional OpenAI-compatible embedding provider.
- Lexical retrieval and reciprocal-rank fusion.
- Vector similarity over stored chunk embeddings when embeddings are enabled.
- Document and chunk provenance in search responses.
- Tests for API behavior, migration setup, restart durability, and revision replacement.
- Python 3.12 GitHub Actions test workflow.

Not yet implemented:
- The remaining POC capability surface listed in [POC capability parity](poc-parity.md).
- Versioned tokenization/analyzer profiles.
- Versioned chunk profiles (Markdown and message profiles).
- Dedicated scalable vector index and vector-store operations.
- Cross-encoder reranking.
- Full POC hybrid-search orchestration and degraded-dependency behavior.
- Source manifest, revision sequencing, and tombstones as a distinct index capability.
- Exact identifier/entity extraction and evidence-backed intent classification.
- Sourced knowledge records and durable episodic memory/session-event ledger.
- Control Center web interface and operational controls.
- Authentication, partition enforcement, ingestion connectors/background jobs, production observability, rate limits, and deployment packaging.

SQLite is the only persistence implementation in this repository. Embeddings are currently stored as JSON and scored in the application; this is not a scalable vector index.
