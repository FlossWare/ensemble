# Implementation status

## First vertical slice

The initial Python application exposes health/readiness, document ingestion, and search through one REST server.

Implemented:
- In-memory document/chunk store for development and tests
- Deterministic character-window chunking
- Optional OpenAI-compatible embedding provider
- Lexical retrieval and reciprocal-rank fusion
- Vector similarity over stored chunk embeddings when embeddings are enabled
- Document and chunk provenance in search responses
- FastAPI-generated OpenAPI document

Not yet implemented:
- Durable persistence
- Dedicated vector index
- Token-aware chunking
- Reranking
- Authentication and partition enforcement
- Ingestion connectors and background jobs

The next implementation step is to establish durable Ensemble-owned persistence and a repeatable indexing lifecycle without exposing storage details through REST contracts.
