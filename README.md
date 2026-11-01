# Ensemble

Ensemble is a standalone, REST-first platform that exposes reusable intelligence capabilities to applications and AI clients.

It does not require Claude, Codex, Cursor, MCP, or any other AI client to run. Those systems are optional consumers.

## Architectural direction

- One application and one REST server initially, with multiple versioned endpoint groups.
- REST is the canonical contract for both external consumers and service-to-service communication.
- Capabilities are independently addressable through REST contracts, even when initially deployed together.
- Service implementations can be composed and decorated while preserving their REST contracts.
- Providers are pluggable and may be multiple independent servers (for example, multiple embedding providers).
- RAG is a platform capability. Retrieval is independently usable; generation is optional and provider-backed.
- MCP is an optional adapter over Ensemble capabilities. It does not contain duplicate business logic.
- The initial storage foundation reuses the active POC data: ChromaDB and SQLite. PostgreSQL is not part of this design.
- The initial deployment stays deliberately simple. Service discovery, federation, service meshes, brokers, and distributed coordination are deferred until needed.

## Consumers

Claude, Codex, Cursor, Crush, traditional applications, and other clients are consumers of Ensemble, not runtime dependencies.

ensemble-claude is a Claude-specific integration built on Ensemble.

## Initial capability areas

The existing POC and related work provide candidate capabilities to consolidate into Ensemble, including memory, graph, knowledge, retrieval, embeddings, reranking, chunking, tokenization, ingestion, indexing, and ranking. Their contracts and implementations will be assessed and consolidated; this list is not a claim that all are already implemented in this repository.

Messaging is explicitly out of the initial scope.

## Documentation

- [Architecture and decisions](docs/architecture.md)

## Status

Architecture-first foundation. Existing POC ingestion is active; preserve it and its data while storage ownership and migration are assessed. Do not interrupt ingestion or create a parallel storage system as part of the initial architecture work.
