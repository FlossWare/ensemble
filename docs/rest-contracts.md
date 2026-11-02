# REST Contract Conventions

**Status:** Canonical conventions for current and future routes. See [the route inventory and naming audit](rest-route-audit.md) for current coverage and cleanup decisions. The running FastAPI application publishes its OpenAPI schema at /openapi.json and interactive documentation at /docs.

## 1. Base path and media type

- Base path: /api/v1.
- JSON request and response bodies use UTF-8.
- REST/HTTP is the canonical public and internal capability boundary.
- Endpoint contracts are independent of implementation language, framework, and deployment location.

## 2. Resource and operation conventions

- Organize paths by caller-facing capability or resource first; place strategy, scope, or representation beneath it.
- A path should express caller intent, not an internal implementation, storage engine, table, collection, class, or provider.
- Use HTTP methods for standard resource operations. Use a subordinate action resource only when the operation is a meaningful domain action that is not ordinary CRUD.
- Use plural resource collections and stable resource identifiers.
- Avoid verb-led paths such as `/add-node`, `/upsert`, or `/delete-superseded` when a resource-oriented path and HTTP method express the operation clearly.
- Keep related operations under a consistent resource root. For example, retrieval is `/search/hybrid`, with a narrower Knowledge scope at `/search/hybrid/knowledge`.
- Do not create alternate aliases for canonical routes. When a route is replaced, update every consumer and remove the old route.
- Health and readiness endpoints belong beneath the capability they describe, using `/health` and `/ready` suffixes.
- Computational capabilities may expose explicit operations when they are naturally requests for a transformation or decision (for example, tokenization, extraction, classification, ranking, and embedding). Prefer resource paths for durable state and CRUD operations.

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
| POST | /api/v1/ingest/documents | Register or replace a document revision, chunk it, optionally embed it, and durably store it |
| POST | /api/v1/ingest/files | Upload a bounded supported file (currently PDF), extract text and ingest it through the shared document pipeline |
| POST | /api/v1/ingest/urls/preview, /api/v1/ingest/urls | Preview or ingest a public HTTP(S) HTML/plain-text page |
| POST | /api/v1/ingest/transcripts | Parse caller-supplied JSONL transcript slices and return a UTF-8 byte checkpoint |
| GET | /api/v1/chunking/health, /ready, /profiles | Chunking capability status and versioned profiles |
| POST | /api/v1/chunking/chunk | Generate deterministic chunks with offsets and provenance |
| POST | /api/v1/search | Lexical retrieval and reciprocal-rank fusion, with vector similarity when configured |
| GET | /api/v1/search/lexical/health, /api/v1/search/lexical/ready | Lexical retrieval capability status |
| POST | /api/v1/search/lexical | Explicit lexical-only retrieval strategy |
| POST | /api/v1/lexical/documents | Existing lexical ingestion convenience; placement remains under audit |
| GET | /api/v1/search/hybrid/health, /api/v1/search/hybrid/ready | Hybrid retrieval capability status |
| POST | /api/v1/search/hybrid | Unified hybrid retrieval |
| POST | /api/v1/search/hybrid/knowledge | Knowledge-scoped hybrid retrieval |
| POST | /api/v1/ingest/sessions | Ingest a structured transcript session and its messages through Memory, Graph, and retrieval indexing |
| POST | /api/v1/memory/events | Capture an idempotent prompt event in the Memory event ledger |
| GET | /api/v1/tokenization/health, /ready, /profiles | Tokenization capability status and analyzer profiles |
| POST | /api/v1/tokenization/tokenize | Versioned deterministic text analysis |
| GET | /api/v1/extraction/health, /ready | Extraction capability status |
| POST | /api/v1/extraction/extract | Exact identifiers and uncertain entity candidates |
| GET | /api/v1/intent/health, /ready | Intent capability status and labels |
| POST | /api/v1/intent/classify | Evidence-backed intent classification |
| GET | /api/v1/knowledge/health, /ready | Knowledge capability status and Index dependency readiness |
| POST | /api/v1/knowledge/records | Publish a sourced record only after Index accepts or confirms its revision |
| GET | /api/v1/knowledge/records/{source_id} | Read the current sourced record and provenance |
| POST | /api/v1/knowledge/records/{source_id}/tombstone | Tombstone a sourced record through Index |
| GET | /api/v1/index/health, /ready | Index capability status |
| POST | /api/v1/index/revisions | Record a revision and return accepted/duplicate/conflict/superseded/tombstoned decision |
| POST | /api/v1/index/tombstones | Permanently tombstone a source at a revision sequence |
| GET | /api/v1/index/sources/{source_id}, /manifest | Read source state and bounded source manifest |
| GET | /api/v1/vector/health, /collections | Vector storage status and collection inventory |
| POST | /api/v1/vector/collections/{name}/records/lookup | Find existing record IDs |
| PUT | /api/v1/vector/collections/{name}/records | Idempotently create or replace vectors and metadata |
| PATCH | /api/v1/vector/collections/{name}/records/metadata | Update metadata for existing vector records |
| POST | /api/v1/vector/collections/{name}/records/prune | Remove superseded vector records for a source |
| POST | /api/v1/vector/collections/{name}/query | Exact dot-product nearest-vector search |
| GET | /api/v1/ranking/health, /ready | Ranking capability status |
| GET | /api/v1/reranking/health, /ready | Local cross-encoder model readiness and provenance |
| POST | /api/v1/reranking/rerank | Score a bounded candidate list with the configured cross-encoder |
| POST | /api/v1/ranking/rank | Explainable reciprocal-rank fusion |
| GET | /api/v1/memory/health, /ready | Memory capability status |
| GET | /api/v1/memory/memories, /memories/{name}, /memories/{name}/entries | List and read memory resources and entries |
| PUT | /api/v1/memory/memories/{name}, /memories/{name}/entries/{event_id} | Replace memory content or append an idempotent event |
| POST | /api/v1/memory/memories/{name}/entries | Append a memory entry |
| POST | /api/v1/memory/search/keywords, /search/semantic, /search/hybrid | Keyword, semantic, and hybrid Memory retrieval |
| POST | /api/v1/memory/recall, /chunking, /events | Context-aware recall, chunking, and prompt event capture |
| POST | /api/v1/memory/sources/import, /sources/reconcile | Import and reconcile sourced Knowledge |
| GET | /api/v1/memory/sources/status | Imported Knowledge source status |

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

## Memory resource conventions

- `GET /api/v1/memory/memories` lists named memory resources.
- `GET /api/v1/memory/memories/{name}` reads a named memory.
- `PUT /api/v1/memory/memories/{name}` replaces its content.
- `POST /api/v1/memory/memories/{name}/entries` appends an entry.
- `PUT /api/v1/memory/memories/{name}/entries/{event_id}` appends an idempotent event identified by its stable event ID.
- `GET /api/v1/memory/memories/{name}/entries` lists bounded entries.
- `POST /api/v1/memory/search/keywords`, `/semantic`, and `/hybrid` expose distinct retrieval strategies.
- `POST /api/v1/memory/recall` performs context-aware recall.
- `POST /api/v1/memory/sources/import` and `/sources/reconcile`, with `GET /api/v1/memory/sources/status`, manage imported Knowledge sources.
- `POST /api/v1/memory/events` captures prompt events.

## Graph resource conventions

- `POST /api/v1/graph/nodes` creates or resolves a node.
- `GET /api/v1/graph/nodes/{node_id}` reads a node by stable identifier.
- `POST /api/v1/graph/edges` creates or resolves an edge.
- `POST /api/v1/graph/query` performs bounded typed node/edge queries.
- `POST /api/v1/graph/traverse` performs bounded directional traversal.

Node and edge mutations use resource names and HTTP methods. Query and traversal remain explicit domain operations because they describe graph computations rather than CRUD resources.

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


## 11. Search API conventions

Search paths are capability-first. `POST /api/v1/search` is the default retrieval contract; `POST /api/v1/search/lexical` requests lexical-only retrieval; `POST /api/v1/search/hybrid` requests the full Hybrid orchestration contract. The Knowledge-scoped Hybrid operation is `POST /api/v1/search/hybrid/knowledge`. The default route returns standard result records; the lexical route explicitly disables vector retrieval and deduplicates identical chunk text; the Hybrid route adds retrieval diagnostics, intent enrichment, and related-session context. The scope segment is present because Knowledge-only retrieval is a distinct caller-facing operation. Do not expose channel implementation details such as FTS5, SQLite, vector collection names, or model providers in search paths. Other strategies or scopes should be added only when a real consumer requirement exists.

## 12. Ingestion API conventions

All source ingestion operations share the `/api/v1/ingest` namespace. Use the source collection (`documents`, `files`, `urls`, `transcripts`) to express the source type; use nested resources such as `/urls/preview` for non-mutating preparation. Format-specific extraction remains an internal adapter concern. The canonical ingestion namespace is the only supported public route structure. New ingestion capabilities must be added beneath this namespace rather than creating parallel source-specific route families.

## 13. Multi-format source ingestion

All source types converge on the same document, chunking, embedding, indexing and retrieval pipeline. Extractors are responsible for source-specific parsing and provenance, not for maintaining separate search implementations.

| Source type | Current support | Provenance retained | Important limits |
|---|---|---|---|
| Plain text / Markdown | Document API; public text URI preview and ingestion | Source ID, title, media type, caller metadata or final URI | URI preview is bounded to 2 MiB and 15 seconds |
| HTML websites | Public HTTP(S) page preview and explicit ingestion | Requested/final URL, title, discovered links | Single page only; discovered links are not crawled automatically |
| PDF | Raw application/pdf upload | Source ID, title, page count and extracted-page count; page markers retained in extracted text | 20 MiB, 1000 pages, 4 million extracted characters; scanned PDFs require OCR |
| JSONL transcripts | Caller-supplied JSONL content slices | Session ID, project, logical source label, message UUID, timestamp, byte checkpoint | 8 MiB per request; only complete newline-terminated records advance the checkpoint |

The JSONL endpoint does not open a caller-supplied server filesystem path. The caller reads the file and submits content plus its current byte offset; the response returns next_offset. Persisting and coordinating checkpoints across clients remains a separate durable-job concern.

Planned source adapters include DOCX, HTML/URL crawl jobs, source-code repositories, email exports, and structured JSON/CSV. OCR, JavaScript-rendered web pages, authenticated sites, broad crawling, and scheduled refresh are not claimed by the current implementation.
