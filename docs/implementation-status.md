# Implementation status

## Implementation decision

Ensemble is implemented in Python. This is an Ensemble-specific choice and does not establish a language standard for the wider FlossWare ecosystem. REST contracts remain language-neutral.

## First vertical slice

The initial Python application exposes health/readiness, document ingestion, and search through one REST server.

Implemented:
- FastAPI application with generated OpenAPI documentation at `/docs`
- In-memory document/chunk store for development and tests
- Deterministic character-window chunking with overlap
- Optional OpenAI-compatible embedding provider
- Lexical retrieval and reciprocal-rank fusion
- Vector similarity over stored chunk embeddings when embeddings are enabled
- Document and chunk provenance in search responses
- Unit and API-level tests
- Python 3.12 GitHub Actions test workflow

Not yet implemented:
- Durable persistence
- Dedicated vector index
- Token-aware chunking
- Reranking
- Authentication and partition enforcement
- Ingestion connectors and background jobs
- Production observability, rate limits, and deployment packaging

The next implementation step is to establish durable Ensemble-owned persistence and a repeatable indexing lifecycle without exposing storage details through REST contracts.

The in-memory store is explicitly non-durable. Do not use it as a production data store.
