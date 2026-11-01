# Implementation status

## Implementation decision

Ensemble is implemented in Python. This is an Ensemble-specific choice and does not establish a language standard for the wider FlossWare ecosystem. REST contracts remain language-neutral.

## Durable persistence milestone

Implemented:
- SQLAlchemy 2 asynchronous persistence adapter.
- SQLite is the default local durable store at .ensemble/ensemble.db.
- PostgreSQL is supported through the same adapter using asyncpg and ENSEMBLE_DATABASE_URL.
- Document and chunk records are stored transactionally.
- Stable document identity for repeated ingestion of the same source and revision.
- Stable chunk identities for a given document revision and ordinal.
- Re-ingestion replaces the indexed chunk set atomically rather than accumulating duplicate chunks.
- Revisions are retained; search returns chunks from the highest revision for each source.
- An older revision arriving late cannot replace a newer revision in the active search view.
- Persistence survives application restart.
- Storage details remain behind the document-store interface and do not appear in REST requests or responses.

The initial schema is created at application startup. Schema versioning and managed migrations are the next persistence-hardening task before production deployment.

## First vertical slice

Implemented:
- FastAPI application with generated OpenAPI documentation at /docs
- Durable SQLite/PostgreSQL document and chunk storage
- Deterministic character-window chunking with overlap
- Optional OpenAI-compatible embedding provider
- Lexical retrieval and reciprocal-rank fusion
- Vector similarity over stored chunk embeddings when embeddings are enabled
- Document and chunk provenance in search responses
- Unit and API-level tests
- Python 3.12 GitHub Actions test workflow

Not yet implemented:
- Managed database schema migrations
- Dedicated vector index
- Token-aware chunking
- Reranking
- Authentication and partition enforcement
- Ingestion connectors and background jobs
- Production observability, rate limits, and deployment packaging

The default SQLite database is suitable for local development and single-instance use. PostgreSQL is the intended backend for a shared multi-client deployment. Vector values are currently stored as portable JSON and scored in the application; this is not yet a scalable vector index.
