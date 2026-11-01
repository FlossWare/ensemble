# REST Contract Conventions

**Status:** Initial conventions. The running FastAPI application publishes its OpenAPI schema at /openapi.json and interactive documentation at /docs.

## 1. Base path and media type

- Base path: /api/v1.
- JSON request and response bodies use UTF-8.
- REST/HTTP is the canonical public and internal capability boundary.
- Endpoint contracts are independent of implementation language, framework, and deployment location.

## 2. Resource and operation conventions

- Use nouns for resources and HTTP methods for operations.
- Use stable resource identifiers in paths.
- Use explicit operation endpoints for domain actions that do not map cleanly to CRUD (for example, POST /api/v1/search and POST /api/v1/rerank).
- Use plural resource names.
- Do not expose database table names, vector-store collection names, filesystem paths, or provider implementation details as public contract requirements.

## 3. Versioning and compatibility

- The major API version is in the URL path.
- Additive optional fields are preferred for compatible evolution.
- Do not silently change the meaning or type of an existing field.
- Breaking changes require a new API version and an explicit migration plan.
- Capability implementations may evolve independently as long as the contract remains compatible.

## 4. Request context

The server derives caller identity and authorized data scope from trusted authentication and authorization context. Callers must not be able to grant themselves access by supplying tenant or partition identifiers in an ordinary request body.

Where useful, request metadata may include a caller-generated correlation ID. Correlation metadata is not an authorization credential.

## 5. Responses and errors

- Return the appropriate HTTP status code.
- Use a consistent Problem Details error representation (RFC 9457) for errors.
- Do not include stack traces, secrets, inaccessible resource metadata, or internal storage details in error responses.
- Return stable error types/codes suitable for programmatic handling.
- Include a correlation identifier when available.

## 6. Operational behavior

- Define explicit request limits and timeouts.
- Use idempotency keys for operations where clients may safely retry a write.
- Paginate collection responses; do not require unbounded result sets.
- Make long-running work explicit rather than holding requests open indefinitely.
- Expose health/readiness separately from business operations.

## 7. Initial implemented endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | /health | Liveness |
| GET | /ready | Application readiness and persistence configuration |
| POST | /api/v1/documents | Register or replace a document revision, chunk it, optionally embed it, and durably store it |
| GET | /api/v1/chunking/health, /ready, /profiles | Chunking capability status and versioned profiles |
| POST | /api/v1/chunking/chunk | Generate deterministic chunks with offsets and provenance |
| POST | /api/v1/search | Lexical retrieval and reciprocal-rank fusion, with vector similarity when configured |
| GET | /api/v1/tokenization/health, /ready, /profiles | Tokenization capability status and analyzer profiles |
| POST | /api/v1/tokenization/tokenize | Versioned deterministic text analysis |
| GET | /api/v1/extraction/health, /ready | Extraction capability status |
| POST | /api/v1/extraction/extract | Exact identifiers and uncertain entity candidates |
| GET | /api/v1/intent/health, /ready | Intent capability status and labels |
| POST | /api/v1/intent/classify | Evidence-backed intent classification |
| GET | /api/v1/ranking/health, /ready | Ranking capability status |
| POST | /api/v1/ranking/rank | Explainable reciprocal-rank fusion |

Ingestion is idempotent for a given source identifier and revision. Re-ingesting that revision replaces its chunk set transactionally. Search returns only the highest registered revision for each source identifier; historical revisions remain stored but are not included in the active search view.

The current document and search implementation remains an initial vertical slice, not the final complete capability surface. FastAPI publishes the machine-readable schema at /openapi.json.

## 8. Required capability endpoint groups

The POC parity inventory in docs/poc-parity.md defines the complete capability baseline. All 13 POC capabilities are required in Ensemble; these endpoint groups are not optional candidates. The exact resource paths and schemas will be specified in versioned contract documents before implementation.

| Capability group | Required operations |
|---|---|
| Embeddings | Model/provider discovery, health/readiness, document and query embedding |
| Vector | Collection discovery, existing-ID lookup, upsert, query, superseded-revision deletion |
| Lexical | Incremental document/session ingestion and ranked search |
| Hybrid | Combined retrieval, knowledge-only retrieval, prompt/event capture and ingestion orchestration |
| Reranking | Bounded cross-encoder reranking and model status |
| Chunking | Profile discovery and deterministic chunk generation |
| Tokenization | Analyzer profile discovery and deterministic tokenization |
| Index | Revision decisions, tombstones, source lookup and bounded manifest listing |
| Extraction | Exact identifier and uncertain entity extraction with evidence |
| Intent | Explainable intent classification with confidence and evidence |
| Ranking | Reciprocal-rank fusion with per-channel contributions |
| Knowledge | Sourced record create/read/tombstone with revision and provenance |
| Memory | Durable memory/event operations, retrieval and operational status |

A Control Center is also a required product surface. It is served by the same application and consumes these REST contracts; it does not create a second backend or duplicate capability logic.

## 9. Search and reranking security

- Authorized scope is resolved server-side.
- Every retrieval channel enforces that scope.
- Graph traversal cannot reveal inaccessible nodes, edges, or metadata.
- Candidate fusion only receives authorized candidates.
- Rerankers receive only authorized candidate text.
- Results are revalidated before being returned.
- Cache keys and cached values are scoped so one caller cannot retrieve another partition's results.

## 10. Decorator compatibility

Decorators wrap capability implementations or REST clients while preserving the same request/response contract. Cross-cutting behavior must not require consumers to call a different API.

Security-sensitive decorators are defense in depth. Enforcement also belongs at the trusted authorization and storage/query boundaries.
