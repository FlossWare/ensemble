# Ensemble

Ensemble is a standalone, REST-first platform for reusable intelligence capabilities. It provides shared memory, knowledge, retrieval, and related capabilities to applications and AI clients.

Ensemble is the product and implementation boundary. It is not a Claude Code extension and does not depend on Claude, Codex, Cursor, Crush, MCP, or any other client to run.

## Architecture

- One application and one REST server initially, with multiple versioned endpoint groups.
- REST is the canonical contract for external consumers and internal capability communication.
- Capabilities are independently addressable through REST contracts, even when co-located in one deployment.
- Implementations are composed and decorated behind stable contracts.
- Providers are pluggable. Embedding and reranking implementations can be selected independently of the API contract.
- Retrieval is independently usable; generation is optional and provider-backed.
- MCP is an optional adapter over REST capabilities. It contains no duplicate business logic.
- Storage technology and migration mechanics are implementation decisions, not public API contracts.

See [Architecture and decisions](docs/architecture.md), [REST contract conventions](docs/rest-contracts.md), and [current implementation status](docs/implementation-status.md).

## Current implementation

The initial Python vertical slice provides health/readiness, document ingestion, chunking, optional embeddings, lexical/vector retrieval, and reciprocal-rank fusion through one FastAPI application. See the implementation status document for what is and is not complete.

The default in-memory store is for development and tests only. Durable Ensemble-owned persistence is the next implementation step.

## Scope

This repository contains the Ensemble platform only.

Client-specific integrations belong in separate consumer repositories (for example, ensemble-claude, ensemble-codex, ensemble-cursor, and ensemble-crush). The broader FlossWare ecosystem is not part of this repository's scope.

The historical claude-ensemble and poc-claude-ensemble repositories are reference material for capability discovery, behavior, and contract compatibility. They are not runtime dependencies or the target implementation.

Existing POC services and data must remain undisturbed during this work. Ensemble must not couple itself to POC process internals, database files, or persistence directories. Any required data migration will use an explicitly designed, supported interface or export/import process.
