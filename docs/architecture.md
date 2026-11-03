# Ensemble Architecture

**Status:** Accepted architectural direction. Implementation status is tracked separately in docs/implementation-status.md and docs/review-readiness.md.

## 1. Purpose and product boundary

Ensemble is a standalone platform for reusable intelligence capabilities, including Memory, Graph, Knowledge, ingestion, indexing, retrieval, embeddings, ranking, and context assembly.

Ensemble owns the shared platform and its canonical contracts. Client-specific integrations belong in separate repositories such as ensemble-claude, ensemble-cursor, ensemble-codex, and ensemble-crush.

Ensemble must operate independently of Claude Code, Codex, Cursor, Crush, MCP, any other client application, and any optional model provider. Historical repositories may provide narrowly scoped implementation evidence when needed, but they are not the product definition, architecture template, or implementation target. Ensemble's scope is determined by its own requirements and contracts.

## 2. Deployment shape

The initial deployment is deliberately simple:

- One application.
- One REST server/listener.
- Multiple versioned endpoint groups.
- Independently addressable logical capabilities.
- Pluggable implementations behind stable contracts.

Capabilities may share a process and deployment initially. Their REST contracts and ownership remain independent so a capability can move to another process or host without changing consumers.

Do not introduce federation, service discovery, service meshes, brokers, distributed coordination, or a microservice fleet without a concrete requirement.

## 3. REST is the canonical boundary

REST/HTTP is the canonical contract for external consumers and capability-to-capability communication.

A capability calls another capability through its REST contract, even when both are co-located. Direct in-process calls are not the normal service boundary. Co-location is a deployment choice, not permission to bypass the contract.

Contracts are language-neutral. Implementations may use the language and runtime suited to the capability; consumers must not depend on implementation language or framework.

The current implementation is not yet fully compliant with this boundary. Some orchestration paths in the review branch still invoke stores and services directly. That gap must be closed as capabilities are consolidated. See docs/review-readiness.md.

See docs/rest-contracts.md for REST contract conventions.

## 4. Composition and decorators

Implementations sit behind capability contracts. Decorators may wrap a REST client or implementation while preserving the same contract.

Cross-cutting decorators may include logging, metrics, tracing, timeouts, bounded retries, caching, provider selection and routing, policy, authorization, and partition-aware access enforcement.

Decorators are composition mechanisms, not the sole security boundary. Authorization must also be enforced at storage and query boundaries where applicable. A capability must not become less secure because a decorator was omitted.

Introduce only the decorators needed by a concrete capability or operational requirement. Do not build a framework of unused extension points.

## 5. Retrieval and RAG

Retrieval is a first-class, independently callable capability. It must work without a generation model or AI client.

The retrieval pipeline may combine lexical retrieval, vector retrieval, Graph expansion, candidate fusion, ranking and reranking, and source metadata and citations.

Reranking receives only candidates the caller is authorized to access. Authorization must be applied before candidate fusion and reranking, and checked again at trust boundaries. Unauthorized content must never be sent to a reranker or external model provider.

Generation is optional and provider-backed. It is not a prerequisite for search, retrieval, or context assembly.

## 6. Data partitions and access control

The architecture must accommodate isolated data partitions without requiring a redesign.

The intended model includes caller identity, owner or tenant identity where applicable, partition identity, classification and sharing policy, explicit grants, and authorized scope carried through ingestion, storage, retrieval, Graph traversal, reranking, caching, and response generation.

Access is deny-by-default. Missing classification or scope must not imply that content is shareable.

Logical partitioning and physical isolation are both possible implementation strategies. Sensitive workloads may require a separate deployment and storage boundary rather than relying only on logical filters.

Authentication and partition enforcement are not prerequisites for the initial local development milestone. They are prerequisites before exposing the service to untrusted networks or multiple mutually untrusted users.

## 7. Storage and migration

Storage technology is not part of the public REST contract.

Ensemble owns its persistence implementation. SQLite is the only persistence implementation in this repository and is the initial supported deployment store. PostgreSQL and other alternative persistence implementations are not part of the current Ensemble scope; shared infrastructure and alternative persistence implementations belong to FlossWare proper.

Ensemble must not depend on another application's database files, persistence directories, process internals, or direct database sharing.

Schema changes are versioned with Alembic and applied as a deployment step before the application starts. Application startup checks that required migrations and tables exist; it does not silently upgrade production schemas.

Existing external data and services must not be disturbed as a prerequisite for implementing Ensemble. Any required import or migration must use an explicitly designed, supported interface or export/import contract. Data migration is separate from implementing the platform's capabilities.

## 8. Providers and adapters

Embedding and ranking providers are pluggable behind Ensemble-owned contracts. Provider-specific behavior must not leak into public API contracts.

Optional generation is provider-backed and must not become a dependency of retrieval or other non-generation capabilities. Provider availability, model identity, and capability support must be explicit. Do not silently switch providers or models when a requested provider is unavailable.

MCP is an optional adapter over existing Ensemble REST operations. REST remains canonical; MCP tools map to existing operations; MCP contains no duplicate business logic; and Ensemble remains fully usable when MCP is disabled.

## 9. Explicitly out of scope initially

- The broader FlossWare ecosystem and its shared infrastructure implementations.
- Client-specific workflows inside the Ensemble platform.
- A mandatory MCP server.
- Federation and distributed service discovery.
- Service mesh, message brokers, and distributed coordination.
- A required generation model.
- Premature provider federation or a model broker.
- Rebuilding or migrating unrelated active data stores as a prerequisite for Ensemble implementation.

## 10. Implementation sequence

1. Finish the architecture and foundational REST contracts.
2. Inventory capabilities and dependencies only as needed to implement the agreed contracts.
3. Establish the application and REST server skeleton.
4. Consolidate capabilities incrementally, including Memory, Graph, Knowledge, ingestion, indexing, embeddings, retrieval/RAG, and context assembly.
5. Integrate the relevant deletion lifecycle within each capability: soft deletion, restoration, purge strategies, and propagation to derived data.
6. Validate data integrity, failure behavior, recovery, and operational behavior.
7. Build client integrations as independent consumers.
8. Revisit generic contracts and broader abstractions only when concrete use cases justify them.

Do not create a separate phase or framework for a concern that belongs inside the capability being implemented.

## 11. Architectural acceptance tests

- Ensemble starts and serves its REST APIs without any AI client installed or running.
- Retrieval works when no generation model is configured or reachable.
- A capability can move from the co-located deployment to another process or host without changing its REST contract or consumer behavior.
- Internal capability calls use the same REST contracts as external calls.
- MCP can be disabled without reducing the REST capability surface.
- Data deletion, restoration, and permanent purge cannot leave stale derived data visible or allow deleted data to be resurrected.


## 12. Test Harness

Ensemble includes a Test Harness contract for synthetic injection, deterministic replay, and controlled fault scenarios through the canonical REST API. It is disabled by default and restricted to a dedicated test deployment and database until end-to-end partition enforcement exists. The harness must use existing ingestion and deletion contracts, retain per-operation outcomes, and never write directly to persistence tables. See [Test Harness](test-harness.md) for its lifecycle, route family, safety requirements, and implementation sequence.
