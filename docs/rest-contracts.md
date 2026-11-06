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

When partition credentials are configured, API callers authenticate with `Authorization: Bearer <token>`. The server hashes the credential with SHA-256 and resolves the principal and authorized partition allow-list from `ENSEMBLE_PARTITION_TOKENS`. Tokens are never accepted from query parameters or request bodies.

`X-Ensemble-Partition` is an optional selector within the authenticated principal's allow-list. It does not grant access. If the token authorizes one partition, that partition is selected automatically; if it authorizes multiple partitions, the caller must select one. An unauthorized selection is rejected before capability execution.

Without a token map, local single-partition mode is limited to the existing `default` database and remote API clients are rejected. Non-loopback listeners require a configured token map. Internal REST calls propagate the selected partition and credential.

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

## 6. Internal capability dispatch

Capability-to-capability calls use the same versioned REST contracts as external consumers, including when capabilities are co-located in the initial application. The initial implementation uses an ASGI-local HTTP transport; this preserves HTTP routing, request validation, and JSON serialization without introducing another process or listener. Hybrid graph expansion also reads current source state through Index and source chunks through the Documents resource contract.

Each capability group can be assigned an independent REST base URL. The initial configuration keys are:

- `ENSEMBLE_SEARCH_REST_URL`
- `ENSEMBLE_MEMORY_REST_URL`
- `ENSEMBLE_INTENT_REST_URL`
- `ENSEMBLE_ENTITIES_REST_URL`
- `ENSEMBLE_GRAPH_REST_URL`
- `ENSEMBLE_RERANKING_REST_URL`
- `ENSEMBLE_EMBEDDINGS_REST_URL`
- `ENSEMBLE_VECTOR_REST_URL`
- `ENSEMBLE_INDEX_REST_URL`
- `ENSEMBLE_DOCUMENTS_REST_URL`
- `ENSEMBLE_KNOWLEDGE_REST_URL`

When a key is unset, requests for that capability use the co-located ASGI transport. Setting a key routes that capability's requests to the configured REST server. Context Assembly dispatches to Search, Memory, Intent, and Entity REST endpoints. Hybrid Search dispatches base retrieval to Search, intent classification and entity extraction to their capabilities, Graph query/traversal to Graph, reranking to Reranking, and its search-ledger write to Memory through REST. Knowledge-scoped Hybrid Search dispatches embedding and vector queries through their REST contracts. Document ingestion dispatches revision registration to Index, document/chunk storage to Documents, entity extraction to Entities, and graph association to Graph through REST. Transcript session ingestion dispatches event persistence to Memory, revision lookup to Index, entity extraction to Entities, and session/file relationships to Graph through REST. Source tombstone propagation dispatches to Documents, Knowledge, and every configured Vector collection through REST. Hybrid Search invokes the Search capability through `POST /api/v1/search/retrieve`. Search retains ownership of its retrieval algorithm and persistence rather than exposing its internal store implementation to Hybrid. Setting `ENSEMBLE_SEARCH_REST_URL` moves Search behind a separate REST server without changing the Hybrid contract. Remaining direct store access inside Search is capability-owned implementation, not cross-capability composition. Only call paths explicitly migrated to the internal REST client satisfy the boundary.

## 7. Initial implemented endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | /health | Liveness |
| GET | /ready | Application readiness and persistence configuration |
| POST | /api/v1/ingest/documents | Register or replace a document revision, chunk it, optionally embed it, and durably store it |
| POST | /api/v1/documents/records | Documents capability resource used by the ingestion orchestrator to store and chunk a validated document |
| POST | /api/v1/documents/sources/tombstone | Tombstone a source in Documents |
| POST | /api/v1/knowledge/sources/tombstone | Tombstone a source in Knowledge |
| POST | /api/v1/vector/collections/{name}/sources/tombstone | Remove all records for a source from one vector collection |
| POST | /api/v1/ingest/files | Upload a bounded supported file (currently PDF), extract text and ingest it through the shared document pipeline |
| POST | /api/v1/ingest/urls/preview, /api/v1/ingest/urls | Preview or ingest a public HTTP(S) HTML/plain-text page |
| POST | /api/v1/ingest/transcripts | Parse caller-supplied JSONL transcript slices and return a UTF-8 byte checkpoint |
| POST | /api/v1/ingest/sessions | Ingest structured transcript sessions into memory, graph, and retrieval |
| GET | /api/v1/chunking/health, /ready, /profiles | Chunking capability status and versioned profiles |
| POST | /api/v1/chunking/chunk | Generate deterministic chunks with offsets and provenance |
| POST | /api/v1/search | Default retrieval using lexical and configured vector channels |
| GET | /api/v1/search/lexical/health, /api/v1/search/lexical/ready | Lexical retrieval capability status |
| POST | /api/v1/search/lexical | Explicit lexical-only retrieval strategy |
| POST | /api/v1/search/retrieve | Execute Search-owned lexical/vector retrieval and return ranked candidates without reranking |
| POST | /api/v1/lexical/documents | Lexical document/session ingestion convenience; placement remains under audit |
| GET | /api/v1/search/hybrid/health, /api/v1/search/hybrid/ready | Hybrid retrieval capability status |
| POST | /api/v1/search/hybrid | Unified hybrid retrieval |
| POST | /api/v1/search/hybrid/knowledge | Knowledge-scoped hybrid retrieval |
| POST | /api/v1/embeddings/embed | Generate query or document embeddings |
| POST | /api/v1/vector/collections/{name}/query | Query a named vector collection |
| POST | /api/v1/memory/events | Capture an idempotent prompt event in the Memory event ledger |
| GET | /api/v1/tokenization/health, /ready, /profiles | Tokenization capability status and analyzer profiles |
| POST | /api/v1/tokenization/tokenize | Versioned deterministic text analysis |
| GET | /api/v1/entities/health, /ready | Entity extraction and Graph-backed identity resolution status |
| POST | /api/v1/entities/extract | Extract typed entity candidates with normalized values, confidence, source offsets, and provenance |
| POST | /api/v1/entities/resolve | Resolve candidates to stable canonical Graph identities; report ambiguous matches |
| POST | /api/v1/entities/link | Create an idempotent typed relationship between two canonical entity identities |
| GET | /api/v1/intent/health, /ready | Intent capability status and supported labels |
| POST | /api/v1/intent/classify | Multi-intent classification with evidence, confidence, constraints, and required capabilities |
| GET | /api/v1/knowledge/health, /ready | Knowledge capability status and Index dependency readiness |
| POST | /api/v1/knowledge/records | Publish a sourced record only after Index accepts or confirms its revision |
| GET | /api/v1/knowledge/records/{source_id} | Read the current sourced record and provenance |
| POST | /api/v1/knowledge/records/{source_id}/tombstone | Tombstone a sourced record through Index |
| GET | /api/v1/index/health, /ready | Index capability status |
| POST | /api/v1/index/revisions | Record a revision and return accepted/duplicate/conflict/superseded/tombstoned decision |
| POST | /api/v1/index/tombstones | Permanently tombstone a source at a revision sequence |
| POST | /api/v1/index/restorations | Restore a soft-deleted source and cancel pending physical reclamation |
| GET | /api/v1/index/reclamation, POST /api/v1/index/reclamation/run | Inspect or explicitly run durable physical reclamation jobs |
| GET | /api/v1/index/sources/{source_id}/deletion-audit | Read bounded append-only deletion strategy and reclamation audit history |
| GET | /api/v1/index/sources/{source_id}, /manifest | Read source state and bounded source manifest |
| POST / GET | /api/v1/collaboration/participants, /participants/{id} | Create and read provider-neutral human and agent identities |
| POST / GET | /api/v1/collaboration/projects, /projects/{id}/members | Create projects and manage membership roles |
| POST / GET | /api/v1/collaboration/projects/{id}/contributions | Create and query attributable project contributions |
| GET | /api/v1/collaboration/projects/{id}/activity | Query project-scoped chronological activity |
| POST / GET | /api/v1/collaboration/projects/{id}/handoffs, /handoffs/{id} | Create and inspect resumable handoffs and history |
| POST | /api/v1/collaboration/handoffs/{id}/checkpoints, /acknowledge, /complete, /supersede | Persist incremental checkpoints and validate handoff lifecycle transitions |
| POST / GET | /api/v1/collaboration/projects/{id}/syntheses, /syntheses/{id}/refresh | Generate and refresh provenance-linked project synthesis |
| POST / GET | /api/v1/collaboration/projects/{id}/proposals, /proposals/{id}/objections, /proposals/{id}/decisions | Preserve competing proposals, objections, and attributable decisions |
| POST | /api/v1/index/sources/query | Read current source state for a bounded set of source identifiers |
| POST | /api/v1/documents/chunks/query | Read chunks for a bounded set of source identifiers with document and chunk provenance |
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
| GET | /api/v1/context/health, /ready | Context assembly capability status |
| POST | /api/v1/context/assemble | Assemble bounded, provenance-preserving Memory and retrieval context with extractive compression |
| GET | /api/v1/memory/memories, /memories/{name}, /memories/{name}/entries | List and read memory resources and entries |
| PUT | /api/v1/memory/memories/{name}, /memories/{name}/entries/{event_id} | Replace memory content or append an idempotent event |
| POST | /api/v1/memory/memories/{name}/entries | Append a memory entry |
| POST | /api/v1/memory/search/keywords, /search/semantic, /search/hybrid | Keyword, semantic, and hybrid Memory retrieval |
| POST | /api/v1/memory/recall, /chunking, /events | Context-aware recall, chunking, and prompt event capture |
| POST | /api/v1/memory/sources/import, /sources/reconcile | Import and reconcile sourced Knowledge |
| GET | /api/v1/memory/sources/status | Imported Knowledge source status |

Ingestion is idempotent for a given source identifier and revision. Re-ingesting that revision replaces its chunk set transactionally. Search returns only the highest registered revision for each source identifier; historical revisions remain stored but are not included in the active search view.

Memory append-once payload identity excludes the event `timestamp` metadata field. Replaying the same event ID and content with a different source timestamp is treated as a duplicate, preserving the first stored event timestamp; changing substantive payload content remains a conflict.

The current document and search implementation remains an initial vertical slice, not the final complete capability surface. FastAPI publishes the machine-readable schema at /openapi.json.

## 8. Required capability endpoint groups

The required capability surface is defined by Ensemble's own product scope, architecture, and capability completion plan. Historical prototypes may provide evidence about useful behavior, but they do not define Ensemble's product boundaries, internal design, or completion criteria.

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
| Entities | Typed entity extraction, canonical resolution, and graph-backed linking |
| Intent | Explainable intent classification with confidence and evidence |
| Ranking | Reciprocal-rank fusion with per-channel contributions |
| Knowledge | Sourced record create/read/tombstone with revision and provenance |
| Memory | Durable memory/event operations, retrieval and operational status |
| Context | Bounded context assembly across Memory and Ensemble retrieval with explicit source provenance and extractive compression |
| Collaboration | Provider-neutral participants, memberships, contributions, handoffs, checkpoints, synthesis, proposals, and auditable human-gated decisions |

Collaboration contracts are documented in [Collaboration REST Contracts](collaboration-contracts.md). The initial implementation uses the existing SQLite database and Control Center; integrations own automatic capture and publish incremental checkpoints through REST.

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

### Session history ledger

Transcript history is stored in the existing `session-events` Memory collection. Consumers use the Memory entry contract directly; there is no separate Ledger service or parallel history route family.

- `GET /api/v1/memory/memories/session-events/entries?limit=1000` reads the bounded session history in chronological order.
- `PUT /api/v1/memory/memories/session-events/entries/{event_id}` appends an entry using a caller-supplied stable event ID.
- Repeating the same event ID and payload returns `duplicate`; reusing that ID with different content returns HTTP `409`.
- Use the idempotent `PUT` form when writing ledger entries so retries cannot create duplicate records.

This contract exposes existing session history and supports controlled appends. It does not introduce event producers, pipeline instrumentation, or additional capture behavior.

## Graph resource conventions

- `POST /api/v1/graph/nodes` creates or resolves a node.
- `GET /api/v1/graph/nodes/{node_id}` reads a node by stable identifier.
- `POST /api/v1/graph/edges` creates or resolves an edge.
- `POST /api/v1/graph/query` performs bounded typed node/edge queries.
- `POST /api/v1/graph/traverse` performs bounded directional traversal.

Node and edge mutations use resource names and HTTP methods. Query and traversal remain explicit domain operations because they describe graph computations rather than CRUD resources.



### Source deletion lifecycle metadata

`POST /api/v1/index/tombstones` accepts optional `actor`, `reason`, and `retain_until` fields in addition to `source_id` and `revision_sequence`.

- `actor` is a bounded contributor identifier. When bearer authentication is enabled, the server derives attribution from the authenticated principal and rejects a conflicting caller-supplied actor. In local mode, callers may supply an explicit actor; otherwise the actor is `local`.
- `reason` is an optional bounded explanation for the deletion.
- `retain_until` is an optional timezone-aware ISO-8601 timestamp. The reclamation job is durably scheduled with this deadline as its earliest eligible execution time. The deadline survives process restarts.
- The reclamation ledger retains actor, reason, request time, retention deadline, retry state, and completion time. Logical tombstoning remains immediate; retention delays only physical reclamation.
- Physical reclamation remains idempotent and retryable. The authoritative Index tombstone is retained after derived payloads are purged.
- The append-only deletion audit ledger records strategy outcomes and worker claims, deferrals, retries, and completion. Read it with `GET /api/v1/index/sources/{source_id}/deletion-audit`.

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

Search paths are capability-first. `POST /api/v1/search` is the default retrieval contract; `POST /api/v1/search/lexical` requests lexical-only retrieval; `POST /api/v1/search/retrieve` is the internal Search capability contract used by Hybrid; `POST /api/v1/search/hybrid` requests the full Hybrid orchestration contract. The Knowledge-scoped Hybrid operation is `POST /api/v1/search/hybrid/knowledge`. The default route returns standard result records; the lexical route explicitly disables vector retrieval and deduplicates identical chunk text; the Hybrid route adds retrieval diagnostics, intent enrichment, and related-session context. The scope segment is present because Knowledge-only retrieval is a distinct caller-facing operation. Do not expose channel implementation details such as FTS5, SQLite, vector collection names, or model providers in search paths. Other strategies or scopes should be added only when a real consumer requirement exists.

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


## Intent and entity contracts

Intent classification is deterministic and explainable. The response retains the primary `label`, `confidence`, `evidence`, and per-label `scores` fields while adding an ordered `intents` collection, extracted `constraints`, `required_capabilities`, and a `classification` method marker. A caller may use the primary label for simple routing or inspect multiple intents and evidence when a request has more than one purpose. Rule-based confidence is a heuristic, not a calibrated probability.

Entity extraction is stateless. Each candidate includes a type, original text, normalized value, stable candidate identifier, character offsets, exact/uncertain marker, confidence, and source provenance when supplied. Candidate identifiers are not canonical entity identities.

Entity resolution uses the Graph capability as its durable identity store. Exact normalized matches resolve to existing canonical entity nodes; otherwise a deterministic canonical node is created. Ambiguous duplicate matches are returned as ambiguous and are not silently selected. The initial resolver performs deterministic exact normalization, not fuzzy or model-based entity resolution.

Entity linking accepts two canonical entity node IDs and a typed relationship. Both endpoints must exist and be entity nodes. Graph remains responsible for durable relationships and traversal. Extraction does not write to Graph, and resolution does not create relationships.

Hybrid Search includes the richer intent result and extracted entity candidates in its response. It does not automatically resolve or persist query entities; callers explicitly invoke resolution when durable identity is required.


## Test Harness

The Test Harness REST route family is specified in [Test Harness](test-harness.md). It is a guarded development and verification capability, disabled by default. Synthetic injection and replay delegate to the existing document/indexing REST pipeline; cleanup delegates to supported deletion contracts. The harness does not define an independent ingestion pipeline or persistent data model visible to normal clients.
