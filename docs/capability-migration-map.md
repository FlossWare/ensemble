# Capability Migration Map

**Status:** Initial inventory from the POC repository's documented and implemented HTTP routes. This is a discovery artifact, not an adoption of POC code or a declaration that every listed capability must survive unchanged.

## Rules

- Ensemble is the only implementation target in this effort.
- POC behavior and HTTP routes are reference evidence only.
- POC services, ingestion, and data remain untouched.
- Do not couple Ensemble to POC databases, persistence paths, local process internals, or service startup.
- Where comparison is useful, use supported POC HTTP endpoints as a reference. Do not use the POC as Ensemble's runtime backend.
- The target contracts below are proposed Ensemble endpoint groups under /api/v1. They are not final schemas until specified in OpenAPI.

## Initial capability mapping

| Capability | POC reference surface | Proposed Ensemble contract area | Initial disposition |
|---|---|---|---|
| Unified search | Hybrid: POST /search and POST /knowledge/search | POST /api/v1/search | Reimplement orchestration and retrieval behind an Ensemble-owned contract |
| Embeddings | POST /embeddings; GET /health and model metadata | POST /api/v1/embeddings | Reimplement provider-neutral embedding contract; provider/runtime is replaceable |
| Vector retrieval | POST /collections/{name}/query | Internal vector retrieval capability behind search | Reimplement; do not expose POC collection naming |
| Lexical retrieval | POST /search and POST /documents | Internal lexical retrieval capability behind search | Reimplement; preserve useful ranking and indexing behavior where validated |
| Ranking / fusion | Ranking service; hybrid search uses reciprocal-rank fusion | Internal candidate-fusion stage in search | Reimplement and test independently |
| Reranking | POST /rerank | POST /api/v1/rerank | Reimplement behind a provider-neutral contract |
| Chunking | POST /chunk | POST /api/v1/chunks or ingestion sub-operation | Reimplement deterministic, versioned chunk profiles where useful |
| Tokenization | POST /tokenize; GET /profiles | POST /api/v1/tokenization or internal ingestion capability | Reimplement versioned analyzer behavior where useful |
| Source index / revisions | POST /revisions, POST /tombstones; GET /sources/{id}, GET /manifest | Document/source revision operations | Reimplement source identity, revision ordering, and tombstone semantics |
| Knowledge records | POST /records; GET /records/{source_id}; POST /records/{source_id}/tombstone | Document/knowledge resources | Reimplement sourced records and revision-aware retrieval |
| Memory | POST /memory/read, /write, /append, /retrieve, /search, /search-hybrid, and related operations | /api/v1/memory | Reassess the operation model; define a clean Ensemble contract rather than cloning every POC route |
| Graph | /graph/add-node, /graph/add-edge, /graph/query, /graph/traverse; GET /graph/node/{id} | /api/v1/graph | Reimplement typed relationships and traversal with authorization-aware boundaries |
| Extraction and intent | Dedicated POC services; exact routes/contracts require further inspection | Ingestion and search sub-capabilities | Assess usefulness and contract needs before committing to endpoint design |

## Implementation status in Ensemble

The Ensemble repository currently contains architecture documentation only. No runtime server, endpoint implementation, OpenAPI specification, persistence adapter, or capability implementation has been established yet.

Therefore every capability in this map is **not yet implemented in Ensemble**. This is intentional: we are defining the target contracts and migration behavior before transferring implementation details.

## First vertical slice

The first useful end-to-end implementation should prove that Ensemble owns the full path:

1. Accept a source document through an Ensemble REST endpoint.
2. Register its identity and revision.
3. Apply a deterministic chunking profile.
4. Request embeddings through the Ensemble embedding contract.
5. Store the source/chunk/index state through Ensemble-owned persistence interfaces.
6. Search through Ensemble's REST search endpoint.
7. Return ranked candidates with source and chunk provenance.

Reranking can be added to this slice after baseline retrieval works. The first slice must not require an AI client, MCP, or POC runtime service.

## Acceptance criteria

- Ensemble starts independently of both POC repositories.
- All capability-to-capability calls cross the documented REST contracts, including when co-located.
- No Ensemble code reads or writes POC storage directly.
- The POC remains operational and its data is unchanged by Ensemble development.
- Search results preserve source identity, revision, and chunk provenance.
- Retrieval and reranking have explicit extension points for authorization and partition scope.
- Evaluation can exercise Ensemble endpoints without importing Ensemble implementation code or depending on POC services.

## Evidence sources

- POC repository README service inventory and documented REST operations.
- POC HTTP handler implementations for hybrid search, embeddings, lexical/vector retrieval, chunking, tokenization, indexing, knowledge, memory, graph, and reranking.
- Ensemble repository architecture and REST contract documents.

Exact POC request/response schemas must be inspected only when the corresponding capability is designed. POC paths are reference paths, not copied Ensemble API paths.
