# Ensemble Architecture

**Status:** Initial architectural direction accepted; implementation and detailed contracts remain to be designed.

## 1. Purpose and ownership

Ensemble is the shared, standalone platform for reusable intelligence capabilities. It is built first.

The platform owns the reusable capabilities and their service contracts. Client-specific repositories own only their client integration and workflows.

- ensemble: shared platform and capabilities.
- ensemble-claude: Claude-specific integration consuming Ensemble.
- Claude, Codex, Cursor, Crush, and other applications are optional clients.
- The historical claude-ensemble and poc-claude-ensemble repositories are source material to assess and consolidate, not assumed to be the long-term platform boundary.

Ensemble must start and operate without Claude Code, Codex, Cursor, MCP clients, or any other AI coding assistant.

## 2. REST is the service contract

REST is the canonical interface for external consumers and internal service-to-service communication.

The initial deployment is:

- One application.
- One REST server/listener.
- Multiple versioned endpoint groups.
- Independently addressable logical capabilities.

A capability invokes another capability through its REST contract, even when both happen to run in the same deployment. Do not substitute in-process calls as the normal service boundary.

This makes service location a deployment/configuration concern rather than a different programming model.

## 3. Co-located first; federation-ready

The initial deployment should remain simple. Capabilities may share one process and one server while retaining independent REST contracts and ownership.

The architecture should permit a capability to move to another host or deployment without changing its consumers or service contract.

Federation is a future possibility, not an initial subsystem. Do not introduce service discovery, distributed registries, service meshes, brokers, distributed coordination, or federation protocols until a concrete requirement warrants them.

## 4. Decorator composition over REST

Service contracts should support decorator composition without coupling consumers to a particular implementation or location.

A decorator may wrap a REST client/implementation while preserving the same service contract. Examples of potential decorators include logging, metrics, tracing, retry, timeout, caching, policy, routing, and failover. These are examples, not a mandate to implement all of them initially.

Provider selection can compose multiple implementations behind one capability. For example, an embedding capability may route requests to multiple independently hosted embedding servers based on model, dimensions, workload, policy, or availability.

## 5. RAG and MCP

### RAG

RAG is a first-class Ensemble capability. Retrieval must be independently usable through REST without requiring a generation model or AI client.

The platform can expose ingestion, extraction, chunking, embedding, lexical/vector retrieval, graph expansion, ranking, reranking, and sourced results as appropriate contracts. Generation is optional and uses a separately configured provider.

### MCP

MCP is an optional adapter over existing Ensemble REST capabilities.

- REST remains canonical.
- MCP tools map to existing capability operations.
- MCP contains no duplicate business logic.
- Ensemble remains fully usable when MCP is disabled.

## 6. Storage and active POC ingestion

The initial storage foundation reuses the existing POC data and storage:

- ChromaDB for existing vector collections.
- SQLite for existing lexical/index/metadata responsibilities.

PostgreSQL is not part of the current design.

The POC ingestion pipeline is actively running. Preserve its operation and existing data. Do not stop ingestion, relocate active storage, re-ingest documents, regenerate embeddings, or create a parallel storage system as an architectural prerequisite.

Before changing storage ownership, inventory the actual deployed databases, collections, schemas, paths, record counts, and write ownership. Avoid assuming database boundaries beyond what the running implementation demonstrates. In particular, avoid having independent processes write to the same ChromaDB persistence directory without a deliberate consistency/ownership design.

Reuse existing data first; assess and consolidate implementation behind Ensemble contracts afterward.

## 7. Capability consolidation

Existing POC and related work should be assessed as candidate Ensemble capabilities rather than automatically retained as separate platforms or repositories. Candidate areas include:

- Memory
- Graph
- Knowledge
- Retrieval and RAG
- Embeddings and provider routing
- Reranking and ranking
- Chunking and tokenization
- Ingestion and extraction
- Indexing and source revision tracking

This is an inventory for assessment, not a claim that every capability is complete or that each requires a separate process.

## 8. Explicitly deferred

- Messaging endpoint/service.
- Federation infrastructure and distributed service discovery.
- Service mesh, message broker, and distributed coordination.
- Mandatory MCP support.
- Dependency on any particular AI client or model vendor.

## 9. Initial implementation sequence

1. Establish the repository architecture and REST contract conventions.
2. Inventory the active POC storage and ingestion without interrupting it.
3. Map existing capabilities, implementations, data ownership, and dependencies into Ensemble boundaries.
4. Define the minimum server and endpoint skeleton.
5. Reuse existing ChromaDB/SQLite data through Ensemble REST contracts.
6. Consolidate capabilities incrementally and validate against existing data.
7. Build ensemble-claude and other clients as independent consumers.

## 10. Architectural test

Can a capability move from the initial co-located Ensemble deployment to another host without changing its consumers or its REST contract?

If yes, the architecture supports the intended future federation without requiring federation machinery today.
