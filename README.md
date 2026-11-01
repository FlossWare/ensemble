# Ensemble

Ensemble is a standalone, REST-first platform for reusable intelligence capabilities. It provides shared memory, knowledge, retrieval, and related capabilities to applications and AI clients.

Ensemble is the product and implementation boundary. It is not a Claude Code extension and does not depend on Claude, Codex, Cursor, Crush, MCP, or any other client to run.

## Architecture

- One application and one REST server initially, with multiple versioned endpoint groups.
- REST is the canonical contract for external consumers and internal service-to-service communication.
- Capabilities are independently addressable through REST contracts, even when co-located in one deployment.
- Implementations are composed and decorated behind stable contracts.
- Providers are pluggable. Embedding and reranking implementations can be selected independently of the API contract.
- Retrieval is independently usable; generation is optional and provider-backed.
- MCP is an optional adapter over REST capabilities. It contains no duplicate business logic.
- Storage technology and migration mechanics are implementation decisions, not public API contracts.

See [Architecture and decisions](docs/architecture.md) and [REST contract conventions](docs/rest-contracts.md).

## Scope

This repository contains the Ensemble platform only.

Client-specific integrations belong in separate consumer repositories (for example, ensemble-claude, ensemble-codex, ensemble-cursor, and ensemble-crush). The broader FlossWare ecosystem is not part of this repository's scope.

The historical claude-ensemble and poc-claude-ensemble repositories are reference material for capability discovery, behavior, and contract compatibility. They are not runtime dependencies or the target implementation.

## Initial capability areas

Capabilities to assess and consolidate include memory, knowledge and document management, ingestion, extraction, chunking, tokenization, embeddings, lexical and vector retrieval, graph relationships, ranking, reranking, and source tracking.

This list describes the intended capability surface, not a claim that these capabilities are already implemented.

## Status

Architecture and contract foundation. Implementation follows after the initial contracts and capability migration map are established.

Existing POC services and data must remain undisturbed during this work. Ensemble must not couple itself to POC process internals, database files, or persistence directories. Any required data migration will use an explicitly designed, supported interface or export/import process.
