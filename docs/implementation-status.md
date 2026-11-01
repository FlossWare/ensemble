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
- Database-side lexical candidate retrieval with a bounded candidate set (maximum 1,000).
- Independent lexical and vector candidate retrieval with reciprocal-rank fusion.\n- PostgreSQL pgvector HNSW cosine index; SQLite exhaustive vector fallback for local development.
- Document and chunk provenance in search responses.

Known limitations:
- Embeddings are stored as JSON and scored in application memory. There is no dedicated vector index.
- Vector similarity is limited to lexical candidates; semantically related documents without lexical overlap can be missed.
- Search candidate pagination is not yet exposed; requests are bounded to at most 1,000 candidates.
- Chunking is character-window based, not token-aware.
- API-key authentication, read/write permissions, and partition enforcement are implemented. API keys are configured through `ENSEMBLE_API_KEYS`; health/readiness are public, while all `/api/v1` routes require bearer authentication. Key rotation, audit logging, rate limiting, and external identity-provider integration remain future hardening.
- Reranking, graph, memory-specific APIs, ingestion connectors/background jobs, production observability, rate limits, and deployment packaging remain future work.

## Compatibility and migration

Ensemble schema changes use reviewed Alembic migrations. Production startup never creates or upgrades schema implicitly. Disposable local development databases may be rebuilt. No Ensemble migration reads, modifies, or imports POC storage. POC databases and services remain outside Ensemble ownership and must remain untouched.

## Verification

GitHub Actions is the authoritative CI gate. Treat a change as verified only after its workflow run succeeds.
