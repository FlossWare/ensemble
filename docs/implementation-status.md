# Implementation status

## Runtime and REST boundaries

Implemented:
- Python 3.12+ FastAPI application with generated OpenAPI.
- One application and one REST listener with versioned endpoint groups.
- Co-located capability calls cross HTTP routing and JSON serialization using ASGI transport.
- Independently addressable tokenization, chunking, embedding, reranking, index, memory, and graph capability endpoints.
- Ingestion calls chunking, embedding, and indexing through REST contracts.
- Search retrieves lexical and vector candidates through REST contracts and applies reciprocal-rank fusion.
- Public health/readiness endpoints and Problem Details errors.

## Authentication and isolation

Implemented:
- Bearer API-key authentication configured through `ENSEMBLE_API_KEYS`.
- Per-key subject, read/write/admin permissions, and allowed partition list.
- `X-Ensemble-Partition` selection and authorization.
- Partition isolation at document identity, ingestion, lexical retrieval, vector retrieval, memory persistence/search, graph nodes, graph edges, and graph traversal.
- Capability-to-capability requests propagate the caller's authentication and partition context.

Remaining hardening:
- Automated key rotation, audit events, rate limits, external identity-provider integration, and delegated service credentials for capabilities moved to separate hosts.

## Persistence and ingestion

Implemented:
- SQLAlchemy 2 asynchronous persistence adapter.
- SQLite local default and PostgreSQL support.
- Alembic-managed schema; startup rejects unmigrated or unsupported schema revisions.
- Transactional document/chunk persistence and stable document/chunk identities.
- Re-ingestion replaces chunk content atomically; revisions are retained.
- Partition-scoped memory upsert/read/search with expiry and importance ordering.
- Partition-scoped graph node/edge upsert and bounded bidirectional traversal.
- Persistence survives application restart.

## Retrieval and model capabilities

Implemented:
- Deterministic Unicode word/punctuation tokenizer (`ensemble-unicode-v1`); this is not model-BPE compatible.
- Character-window chunking with overlap.
- Optional OpenAI-compatible embedding provider.
- PostgreSQL pgvector HNSW cosine index; SQLite exhaustive local vector fallback.
- Bounded database-side lexical candidates and independently bounded vector candidates.
- Reciprocal-rank fusion and document/chunk provenance.
- Cohere-compatible reranking adapter, reported unavailable when no provider is configured.

Known limitations:
- Reranking capability is not yet integrated into the public search pipeline.
- Chunking is character-window based, not token-aware.
- Memory search is lexical; semantic memory retrieval is not yet implemented.
- Graph traversal is bounded and returns a neighborhood; graph query language, typed constraints, and graph/vector hybrid retrieval are not implemented.
- Search pagination, request budgets, query timeouts, production metrics/tracing, ingestion connectors/background jobs, and deployment packaging remain future work.

## Compatibility and migration

Ensemble schema changes use reviewed Alembic migrations. Production startup never creates or upgrades schema implicitly. No Ensemble migration reads, modifies, or imports POC storage. POC databases and services remain outside Ensemble ownership and must remain untouched.

## Verification

GitHub Actions is the authoritative CI gate. Treat a change as verified only after its workflow run succeeds.
