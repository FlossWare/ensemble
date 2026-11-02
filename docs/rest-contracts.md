# REST Contract Conventions

**Status:** Versioned REST capability contracts. FastAPI publishes OpenAPI at `/openapi.json` and interactive documentation at `/docs`.

## Authentication and partition scope

Every `/api/v1` endpoint requires `Authorization: Bearer <api-key>`. `ENSEMBLE_API_KEYS` maps each secret key to a subject, allowed partition list, and `read`/`write`/`admin` permissions. Requests use `X-Ensemble-Partition` to select a partition. If a key has exactly one permitted partition, the header may be omitted. A partition outside the key's allow-list returns 403. Health and readiness endpoints are public.

Internal capability calls propagate the authenticated caller's bearer key and resolved partition. Capability handlers apply the partition at the persistence boundary; callers cannot choose an arbitrary partition through request bodies.

## Conventions

- Base path: `/api/v1`; JSON request/response bodies use UTF-8.
- REST/HTTP is canonical for public and internal capability communication.
- Contracts are independent of implementation language, framework, and deployment location.
- Prefer additive optional fields. Breaking changes require a new API version and an explicit migration plan.
- Do not expose database table names, filesystem paths, or provider implementation details as public contract requirements.
- Use appropriate status codes and Problem Details errors. Do not expose stack traces, secrets, or internal storage details.
- Correlation metadata is not an authorization credential.

## Implemented endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Public liveness |
| GET | `/ready` | Public persistence/provider readiness |
| POST | `/api/v1/documents` | Ingest or revise a document through chunking, embeddings, and indexing |
| POST | `/api/v1/search` | Retrieve lexical and vector candidates and fuse rankings |
| POST | `/api/v1/capabilities/tokenization/tokenize` | Deterministic Unicode tokenization (`ensemble-unicode-v1`) |
| POST | `/api/v1/capabilities/chunking/split` | Deterministic character-window chunking |
| POST | `/api/v1/capabilities/embeddings` | Provider-neutral embedding capability |
| POST | `/api/v1/capabilities/reranking/rerank` | Cohere-compatible reranking; 503 if no provider is configured |
| POST | `/api/v1/capabilities/index/documents` | Persist a document and its chunks |
| POST | `/api/v1/capabilities/index/search-candidates` | Bounded lexical candidate retrieval |
| POST | `/api/v1/capabilities/index/vector-search` | Bounded nearest-vector retrieval |
| POST | `/api/v1/capabilities/memory` | Upsert a partition-scoped memory |
| GET | `/api/v1/capabilities/memory/{memory_key}` | Read a non-expired memory |
| POST | `/api/v1/capabilities/memory/search` | Search non-expired memories in the active partition |
| POST | `/api/v1/capabilities/graph/nodes` | Upsert a partition-scoped graph node |
| POST | `/api/v1/capabilities/graph/edges` | Upsert an edge between nodes in the active partition |
| GET | `/api/v1/capabilities/graph/nodes/{node_id}/neighbors` | Traverse a bounded graph neighborhood, depth 1-4 |

## Persistence and retrieval semantics

Ingestion is idempotent for a partition, source identifier, and revision. Re-ingesting that revision replaces its chunk set transactionally. Search returns only the highest revision for each source within the active partition.

PostgreSQL uses pgvector with an HNSW cosine index for vector candidates. Set `ENSEMBLE_EMBEDDING_DIMENSIONS` to the embedding model's output dimension before applying migrations. SQLite uses an exhaustive local vector fallback. Lexical and vector candidates are independently bounded to at most 1,000 before reciprocal-rank fusion.

Memory upsert is idempotent by partition and memory key. Expired memories are excluded from reads and search. Graph node identity is stable by partition, node type, and label. Edges can only connect nodes within the active partition. Graph traversal is bounded to four hops.

## Planned hardening

Tokenization is a deterministic Unicode word/punctuation tokenizer, not a model-specific BPE tokenizer. Chunking remains character-window based. Reranking is available as a capability but is not yet wired into the public search pipeline. Pagination, request budgets, timeouts, audit events, key rotation automation, rate limits, and external identity-provider integration remain future hardening.
