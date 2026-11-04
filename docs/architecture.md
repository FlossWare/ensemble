# Ensemble Architecture

**Status:** Initial architectural direction accepted; implementation and detailed contracts remain to be designed.

## 1. Purpose and ownership

Ensemble is the shared, standalone platform for reusable intelligence capabilities. It is built first.

The platform owns reusable capabilities and their service contracts. Client-specific repositories own only their client integration and workflows.

- ensemble: shared platform and capabilities.
- ensemble-claude: Claude-specific integration consuming Ensemble.
- Claude Code, Codex, Cursor, Crush, and other applications are optional clients.
- The historical claude-ensemble and poc-claude-ensemble repositories are source material to assess and consolidate, not assumed to be the long-term platform boundary.

Ensemble must start and operate without Claude Code, Codex, Cursor, MCP clients, or any other AI coding assistant installed, authenticated, or running.

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

## 5. Model access and client boundary

Model inference is an Ensemble capability, not an AI-client integration.

- Ensemble owns a provider-neutral model invocation contract, model catalog, explicit routing, and usage records.
- Provider adapters implement external inference protocols. The Anthropic Messages API is one adapter protocol, not Ensemble's universal internal contract.
- EnMaaS is the first identified enterprise model gateway and the first candidate provider adapter. Its Anthropic Messages API compatibility does not make Claude Code a dependency.
- Other gateways and direct providers may be added through adapters without changing consumers of Ensemble's model-access REST contract.
- Enterprise and personal provider profiles, credentials, model allowlists, and policies must remain isolated.
- Model selection must be explicit. Unknown, unavailable, or unauthorized models fail clearly; there is no silent provider or model fallback.
- Gateway discovery informs the catalog but does not itself establish workload approval or authorization.
- Ensemble must not duplicate or bypass upstream gateway authorization and enterprise policy.

**AI clients are consumers, not model-provider implementations.** Claude Code, Codex, Cursor, Crush, and similar applications may call Ensemble APIs, but they do not belong in Ensemble's core runtime or provider registry.

In particular:

- No ClaudeCodeProvider, Claude CLI subprocess, Claude authentication check, or Claude-specific runtime selection belongs in Ensemble core.
- No Claude installation, account, token, or configuration is required to start or operate Ensemble.
- If a client-specific integration is needed, it belongs in its own integration project (for example, ensemble-claude).
- A client may use its own model gateway independently while consuming Ensemble memory, knowledge, retrieval, or other capabilities. Using Ensemble does not require routing that client's inference through Ensemble.

See [Model Access](model-access.md) for the initial provider boundary and EnMaaS direction.

## 6. RAG and MCP

### RAG

RAG is a first-class Ensemble capability. Retrieval must be independently usable through REST without requiring a generation model or AI client.

The platform can expose ingestion, extraction, chunking, embedding, lexical/vector retrieval, graph expansion, ranking, reranking, and sourced results as appropriate contracts. Generation is optional and uses a separately configured provider.

### MCP

MCP is an optional adapter over existing Ensemble REST capabilities.

- REST remains canonical.
- MCP tools map to existing capability operations.
- MCP contains no duplicate business logic.
- Ensemble remains fully usable when MCP is disabled.

## 7. Storage and active POC ingestion

The initial storage foundation reuses the existing POC data and storage:

- ChromaDB for existing vector collections.
- SQLite for existing lexical/index/metadata responsibilities.

PostgreSQL is not part of the current design.

The POC ingestion pipeline is actively running. Preserve its operation and existing data. Do not stop ingestion, relocate active storage, re-ingest documents, regenerate embeddings, or create a parallel storage system as an architectural prerequisite.

Before changing storage ownership, inventory the actual deployed databases, collections, schemas, paths, record counts, and write ownership. Avoid assuming database boundaries beyond what the running implementation demonstrates. In particular, avoid having independent processes write to the same ChromaDB persistence directory without a deliberate consistency/ownership design.

Reuse existing data first; assess and consolidate implementation behind Ensemble contracts afterward.

## 8. Capability consolidation

Existing POC and related work should be assessed as candidate Ensemble capabilities rather than automatically retained as separate platforms or repositories. Candidate areas include:

- Memory
- Graph
- Knowledge
- Retrieval and RAG
- Model access and provider routing
- Embeddings and provider routing
- Reranking and ranking
- Chunking and tokenization
- Ingestion and extraction
- Indexing and source revision tracking

This is an inventory for assessment, not a claim that every capability is complete or that each requires a separate process.

## 9. Explicitly deferred

- Messaging endpoint/service.
- Federation infrastructure and distributed service discovery.
- Service mesh, message broker, and distributed coordination.
- Mandatory MCP support.
- Dependency on any particular AI client or model vendor.
- Anthropic-compatible public API facade over Ensemble's provider-neutral model contract, unless a concrete consumer requires it.
- Streaming, tool use, and advanced model features until provider-specific support and requirements are verified.

## 10. Initial implementation sequence

1. Establish the repository architecture and REST contract conventions.
2. Inventory the active POC storage and ingestion without interrupting it.
3. Map existing capabilities, implementations, data ownership, and dependencies into Ensemble boundaries.
4. Define the minimum server and endpoint skeleton.
5. Define provider-neutral model-access contracts and the EnMaaS adapter boundary.
6. Reuse existing ChromaDB/SQLite data through Ensemble REST contracts.
7. Consolidate capabilities incrementally and validate against existing data.
8. Build ensemble-claude and other clients as independent consumers.

## 11. Architectural tests

The architecture must pass these tests:

1. Can Ensemble start, become healthy, and serve its REST APIs when Claude Code is not installed?
2. Can Ensemble start and serve non-model capabilities when no model provider is configured or reachable?
3. Can a capability move from the initial co-located deployment to another host without changing its consumers or REST contract?
4. Can Claude Code, Crush, Cursor, or another client consume Ensemble without becoming a runtime dependency?
5. Can a model provider be replaced or added without changing the provider-neutral model-access contract?

If yes, the architecture preserves the intended independence and future deployment flexibility without requiring premature federation machinery.
