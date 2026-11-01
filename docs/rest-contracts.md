# REST Contract Conventions

**Status:** Initial versioned contracts. FastAPI publishes OpenAPI at `/openapi.json` and interactive documentation at `/docs`.

## Authentication and partition scope

Every `/api/v1` endpoint requires `Authorization: Bearer <api-key>`. `ENSEMBLE_API_KEYS` maps each secret key to a subject, allowed partition list, and `read`/`write`/`admin` permissions. Requests use `X-Ensemble-Partition` to select a partition. If a key has exactly one permitted partition, the header can be omitted. A requested partition outside the key's allow-list returns 403. Health and readiness endpoints are public. Internal capability calls propagate the authenticated caller identity and partition rather than bypassing the boundary.

## Conventions

- Base path: `/api/v1`; JSON request/response bodies use UTF-8.
- REST/HTTP is canonical for public and internal capability communication.
- Contracts are independent of implementation language, framework, and deployment location.
- Prefer additive optional fields. Breaking changes require a new API version and an explicit migration plan.
- Do not expose database table names, filesystem paths, or provider implementation details as public contract requirements.
- Use appropriate status codes and Problem Details errors. Do not expose stack traces, secrets, or internal storage details.
- Correlation metadata is not an authorization credential.
- Authentication must derive caller identity and authorized scope from trusted context. That control is not yet implemented.

## Implemented endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/ready` | Application readiness and persistence |
| POST | `/api/v1/documents` | Ingest/revise a document through chunking, embeddings, and indexing contracts |
| POST | `/api/v1/search` | Retrieve candidates and fuse lexical/vector rankings |
| POST | `/api/v1/capabilities/chunking/split` | Deterministic chunking capability |
| POST | `/api/v1/capabilities/embeddings` | Provider-neutral embedding capability |
| POST | `/api/v1/capabilities/index/documents` | Persist a document and its chunks |
| GET | `/api/v1/capabilities/index/chunks` | Return current indexed chunks with provenance |

Capability routes are independently addressable and called over HTTP by ingestion/search orchestration. They are co-hosted today. All versioned routes require authentication and enforce partition membership. The initial key provider is environment-backed; use TLS at the deployment edge and rotate keys through deployment configuration.

Ingestion is idempotent for a source identifier and revision. Re-ingesting that revision replaces its chunk set transactionally. Search returns only the highest registered revision for each source identifier.

## Planned capabilities

Memory, graph, reranking, tokenization, extraction, and ingestion jobs remain planned. Specify and test each contract before implementing it. Index candidate retrieval is bounded (maximum 1,000 candidates) and uses database-side lexical matching. PostgreSQL uses a pgvector HNSW index for independent vector candidates; SQLite uses an exhaustive local fallback. PostgreSQL migration requires `ENSEMBLE_EMBEDDING_DIMENSIONS` to match the embedding model. Both candidate channels are independently bounded.

## Security requirements

Authorized scope is resolved from the API key allow-list and `X-Ensemble-Partition`; it is applied at the persistence query boundary for lexical and vector retrieval before fusion. Future caches and rerankers must preserve this scope.
