# REST Contract Conventions

**Status:** Initial conventions. The running FastAPI application currently publishes its OpenAPI schema at `/openapi.json`; the schemas will be promoted to a reviewed, version-controlled OpenAPI artifact as the API stabilizes.

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
| GET | /ready | Current application readiness |
| POST | /api/v1/documents | Register a document, chunk it, optionally embed it, and store it |
| POST | /api/v1/search | Lexical retrieval and reciprocal-rank fusion, with vector similarity when configured |

The current document and search implementation is an initial vertical slice, not the final complete capability surface. FastAPI publishes the current machine-readable schema at `/openapi.json`.

## 8. Planned endpoint groups

These are candidate groups, not a claim that they are implemented:

| Group | Initial purpose |
|---|---|
| /capabilities | Discover enabled capabilities and contract versions |
| /ingestion | Submit and inspect ingestion work |
| /embeddings | Generate embeddings through configured providers |
| /rerank | Rerank an explicitly supplied authorized candidate set |
| /memory | Store and recall scoped memories |
| /graph | Inspect and traverse authorized relationships |

Do not create every endpoint merely because it appears in this candidate list.

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
