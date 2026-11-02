# Capability Migration Map

**Status:** Reference inventory and current Ensemble implementation map. POC routes and behavior are evidence only, not runtime dependencies or a requirement to clone every historical endpoint.

## Rules

- Ensemble is the only implementation target.
- POC services, ingestion, and data remain untouched.
- Do not couple Ensemble to POC databases, persistence paths, local process internals, or service startup.
- Use supported POC HTTP endpoints as behavioral references where useful.
- Ensemble contracts are independent of POC route names and payloads.

## Capability map

| Capability | POC reference | Ensemble contract | Status |
|---|---|---|---|
| Document ingestion | Knowledge/index/revision APIs | `POST /api/v1/documents` | Implemented |
| Extraction | Dedicated POC extraction service | `POST /api/v1/capabilities/extraction/text` | Implemented plain text, Markdown, HTML/XHTML extraction |\n| Tokenization | Tokenizer service | `POST /api/v1/capabilities/tokenization/tokenize` | Implemented deterministic Unicode tokenizer; not model-BPE compatible |
| Chunking | `POST /chunk` | `POST /api/v1/capabilities/chunking/split` and `/split-tokens` | Implemented character and Unicode-token window profiles |
| Embeddings | `POST /embeddings` | `POST /api/v1/capabilities/embeddings` | Implemented provider adapter |
| Reranking | `POST /rerank` | `POST /api/v1/capabilities/reranking/rerank` | Implemented Cohere-compatible adapter; provider required |
| Index persistence | Index/revision APIs | `POST /api/v1/capabilities/index/documents` | Implemented relational persistence |
| Lexical retrieval | Lexical/hybrid search | `POST /api/v1/capabilities/index/search-candidates` | Implemented bounded database-side candidate retrieval |
| Vector retrieval | Vector query | `POST /api/v1/capabilities/index/vector-search` | PostgreSQL pgvector HNSW; SQLite exhaustive fallback |
| Unified search | Hybrid search | `POST /api/v1/search` | Implemented lexical/vector candidate fusion |
| Memory | Memory read/write/retrieve APIs | `/api/v1/capabilities/memory` | Implemented partition-scoped upsert/read/search with expiry |
| Graph | Graph add/query/traverse APIs | `/api/v1/capabilities/graph` | Implemented partition-scoped nodes/edges and bounded traversal |
| Extraction and intent | Dedicated POC services | To be specified | Not implemented |
| Ingestion jobs | POC ingestion orchestration | To be specified | Not implemented |

## Current vertical slice

1. Accept a source document through Ensemble REST.
2. Call chunking over its REST contract.
3. Request embeddings through the embedding capability when configured.
4. Persist source identity, revision, chunks, and vectors through the index capability.
5. Retrieve bounded lexical and vector candidates independently.
6. Fuse candidates and return source/chunk provenance.

Capabilities are co-hosted and use ASGI-backed HTTP transport today. The REST contracts, not transport location, define the boundary.

## Remaining engineering work

- Integrate optional reranking into the public search pipeline.
- Add token-aware chunking and semantic memory retrieval.
- Add extraction, ingestion connectors, and background jobs.
- Add graph query constraints and graph/vector hybrid retrieval where justified.
- Add pagination, request budgets, timeouts, observability, and deployment packaging.
- Validate quality and performance with the independent Evaluation project.

## Acceptance

Ensemble starts independently of both POC repositories. Capability-to-capability calls cross documented REST contracts, including when co-located. No Ensemble code reads or writes POC storage. POC services and data remain unchanged. Retrieval and knowledge operations preserve partition scope and provenance.
