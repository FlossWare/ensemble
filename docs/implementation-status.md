# Implementation status

## REST and runtime foundation

Implemented:
- Python 3.12+ FastAPI application with generated OpenAPI.
- One application and one REST listener with versioned endpoint groups.
- Co-located capability calls cross HTTP routing and JSON serialization using ASGI transport.
- Independently addressable chunking, embeddings, and index capability endpoints.
- Ingestion calls chunking, embedding, and indexing over those REST contracts.
- Search retrieves indexed chunks and query embeddings through capability REST contracts.
- Health/readiness and Problem Details error responses.

## Persistence and ingestion

Implemented:
- SQLAlchemy 2 asynchronous persistence adapter.
- SQLite default at .ensemble/ensemble.db and PostgreSQL support via asyncpg.
- Alembic-managed schema; startup rejects an unmigrated or incomplete database.
- Transactional document/chunk persistence.
- Stable document identity for repeated ingestion of the same source and revision.
- Stable chunk identities for a document revision and ordinal.
- Re-ingestion replaces the indexed chunk set atomically.
- Revisions are retained; search returns the highest revision for each source.
- Persistence survives application restart.

## Retrieval

Implemented:
- Deterministic character-window chunking with overlap.
- Optional OpenAI-compatible embedding provider.
- Lexical retrieval, optional cosine similarity, and reciprocal-rank fusion.
- Document and chunk provenance in search responses.

Known limitations:
- Embeddings are stored as JSON and scored in application memory. There is no dedicated vector index.
- Search currently loads the current indexed chunk set; bounded candidate retrieval and pagination remain future work.
- Chunking is character-window based, not token-aware.
- Authentication, tenant/partition enforcement, sharing policy, and authorization-aware retrieval are not implemented. Do not expose this as a multi-user shared service until those controls exist.
- Reranking, graph, memory-specific APIs, ingestion connectors/background jobs, production observability, rate limits, and deployment packaging remain future work.

## Compatibility and migration

Ensemble schema changes use reviewed Alembic migrations. Production startup never creates or upgrades schema implicitly. Disposable local development databases may be rebuilt. No Ensemble migration reads, modifies, or imports POC storage. POC databases and services remain outside Ensemble ownership and must remain untouched.

## Verification

GitHub Actions is the authoritative CI gate. Treat a change as verified only after its workflow run succeeds.
