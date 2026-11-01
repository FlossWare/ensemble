# REST Contract Conventions

**Status:** Initial versioned contracts. FastAPI publishes OpenAPI at `/openapi.json` and interactive documentation at `/docs`.

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

Capability routes are independently addressable and called over HTTP by ingestion/search orchestration. They are co-hosted today. Authentication is not implemented, so do not expose these routes as a trusted multi-user service.

Ingestion is idempotent for a source identifier and revision. Re-ingesting that revision replaces its chunk set transactionally. Search returns only the highest registered revision for each source identifier.

## Planned capabilities

Memory, graph, reranking, tokenization, extraction, and ingestion jobs remain planned. Specify and test each contract before implementing it. The current index enumeration is unbounded and vector scoring scans stored JSON embeddings; bounded retrieval and a dedicated vector index are required before unbounded corpus growth.

## Security requirements

The target design resolves authorized scope server-side, applies scope in every retrieval channel, filters candidates before fusion/reranking, and scopes caches. These controls are not implemented in the initial vertical slice.
