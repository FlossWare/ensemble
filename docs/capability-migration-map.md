# Capability Migration Map

**Status:** Reference inventory and current Ensemble implementation map. This is not adoption of POC code or a requirement to clone every historical endpoint.

## Rules

- Ensemble is the only implementation target.
- POC behavior and HTTP routes are reference evidence only.
- POC services, ingestion, and data remain untouched.
- Do not couple Ensemble to POC databases, persistence paths, local process internals, or service startup.
- Use supported POC HTTP endpoints as behavioral references where useful.
- Ensemble contracts are independent of POC route names and payloads.

## Capability map

| Capability | POC reference | Ensemble contract | Status |
|---|---|---|---|
| Document ingestion | Knowledge/index/revision APIs | `POST /api/v1/documents` | Implemented orchestration |
| Tokenization | Tokenizer service | `POST /api/v1/capabilities/tokenization/tokenize` | Implemented deterministic Unicode tokenizer; not model-BPE compatible |\n| Reranking | `POST /rerank` | `POST /api/v1/capabilities/reranking/rerank` | Implemented Cohere-compatible adapter; provider configuration required |\n| Chunking | `POST /chunk` | `POST /api/v1/capabilities/chunking/split` | Implemented character-window profile |
| Embeddings | `POST /embeddings` | `POST /api/v1/capabilities/embeddings` | Implemented provider adapter |
| Index persistence | Index/revision APIs | `POST /api/v1/capabilities/index/documents` | Implemented relational persistence |
| Current chunk enumeration | Vector/lexical retrieval | `GET /api/v1/capabilities/index/chunks` | Implemented; currently unbounded |
| Unified search | Hybrid search | `POST /api/v1/search` | Implemented lexical/vector scoring and RRF |
| Dedicated vector retrieval | Vector query | Future capability | Not implemented; JSON scan only |
| Reranking | `POST /rerank` | Future `/api/v1/rerank` | Not implemented |
| Tokenization | `POST /tokenize` | To be specified | Not implemented |
| Memory | Memory read/write/retrieve APIs | Future `/api/v1/memory` | Not implemented |
| Graph | Graph add/query/traverse APIs | Future `/api/v1/graph` | Not implemented |
| Extraction and intent | Dedicated POC services | To be assessed | Not implemented |

## Current vertical slice

1. Accept a source document through Ensemble REST.
2. Call chunking over its REST contract.
3. Request embeddings through the embedding capability when configured.
4. Persist source identity, revision, chunks, and vectors through the index capability.
5. Search through Ensemble REST, retrieving indexed candidates and query embeddings through capability REST contracts.
6. Return ranked candidates with source and chunk provenance.

Capabilities are co-hosted and use ASGI-backed HTTP transport today. The REST contracts, not transport location, define the boundary.

## Remaining engineering work

- Replace full chunk enumeration and in-process vector scoring with bounded lexical/vector retrieval and a real vector index.
- Add authentication and enforce partitions at every retrieval/storage boundary before multi-user exposure.
- Add reranking, graph, memory, tokenization, extraction, and ingestion job contracts based on concrete use cases.
- Add pagination, request budgets, timeouts, metrics, and deployment packaging.
- Validate quality and performance with the independent Evaluation project.

## Acceptance

Ensemble starts independently of both POC repositories. Capability-to-capability calls cross documented REST contracts, including when co-located. No Ensemble code reads or writes POC storage. POC services and data remain unchanged. Search results preserve source identity, revision, and chunk provenance. Evaluation exercises Ensemble endpoints without importing implementation code or depending on POC services.
