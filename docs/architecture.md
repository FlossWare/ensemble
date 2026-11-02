# Ensemble Architecture

**Status:** Foundational direction. Contracts are being established before implementation.

## 1. Purpose and scope

Ensemble is the standalone platform for reusable intelligence capabilities: memory, knowledge, retrieval, embeddings, ranking, reranking, and related operations.

This repository owns the platform and its implementations. Client-specific integrations and workflows belong in separate consumer repositories.

The scope is Ensemble only. This is not a plan to consolidate the wider FlossWare ecosystem.

The historical claude-ensemble and poc-claude-ensemble repositories are functional references. The POC is the capability baseline: every service/capability in its catalog must be accounted for in Ensemble parity. The POC's algorithms and behavior are evidence; its process topology, storage choices, and implementation details are not binding.

Ensemble must operate without Claude Code, Codex, Cursor, Crush, MCP, or any other AI client.

## 2. Deployment shape

The initial deployment is deliberately simple:

- One application.
- One REST server/listener.
- Multiple versioned endpoint groups.
- Independently addressable logical capabilities.
- Pluggable implementations behind stable contracts.

Capabilities may share a process and deployment initially. They retain independent REST contracts and ownership so they can be moved to another process or host later without changing consumers.

Do not introduce federation, service discovery, service meshes, brokers, distributed coordination, or a microservice fleet without a concrete requirement.

## 3. REST is the canonical boundary

REST/HTTP is the canonical contract for both external consumers and internal capability-to-capability communication.

A capability calls another capability through its REST contract, even when both are co-located. Do not make direct in-process calls the normal service boundary. This keeps deployment topology from changing the programming model.

Contracts are language-neutral. Implementations may use the language and runtime best suited to the capability, but clients must not depend on implementation language or framework.

See REST contract conventions in docs/rest-contracts.md.

## 4. Composition and decorators

Implementations sit behind capability contracts. Decorators may wrap a REST client or implementation while preserving the same contract.

Expected cross-cutting concerns include logging, metrics, tracing, timeouts, bounded retries, caching, provider selection and routing, policy, authorization, and partition-aware access enforcement.

Decorators are composition mechanisms, not the sole security boundary. Authorization must also be enforced at the storage/query boundary where applicable. A capability must not become less secure because a decorator was accidentally omitted.

## 5. Retrieval and RAG

Retrieval is a first-class, independently callable capability. It must work without a generation model or AI client.

The retrieval pipeline may combine lexical retrieval, vector retrieval, graph expansion, candidate fusion, ranking and reranking, and source metadata and citations.

Reranking receives only candidates that the caller is authorized to access. Authorization is applied before candidate fusion and reranking, and checked again at trust boundaries. Unauthorized content must never be sent to a reranker or external model provider.

Generation is optional and provider-backed. It is not a prerequisite for search or retrieval.

## 6. Data partitions and access control

The architecture must accommodate multiple users and isolated data partitions without requiring a redesign.

The intended model includes tenant or owner identity, partition identity, classification and sharing policy, membership and explicit sharing grants, and authorized scope carried through ingestion, storage, retrieval, graph traversal, reranking, caching, and response generation.

Access is deny-by-default. A missing classification is not implicitly shareable.

Logical partitioning and physical isolation are both possible implementation strategies. Sensitive or proprietary information may require a separate deployment and storage boundary rather than relying solely on logical filters.

The full multi-user sharing and partition implementation is a later capability. The initial contracts and metadata model must not prevent it.

## 7. Storage and migration

Storage technology is not part of the public REST contract.

Ensemble owns its own SQLite persistence implementation. SQLite is the only persistence implementation in this repository and is used for local and initial single-instance deployments. PostgreSQL, other alternative persistence implementations, and shared persistence infrastructure belong to FlossWare proper; they are not planned or implemented here.

Ensemble must not depend on POC database files, persistence directories, process internals, or direct database sharing.

Schema changes are versioned with Alembic and applied as a deployment step before the application starts. Application startup checks that the migration table and required Ensemble tables exist; it does not create or silently upgrade production schemas.

Use the POC's supported REST endpoints as a behavioral and contract reference when useful. Do not inspect or couple to its physical storage as a prerequisite for building Ensemble.

Existing POC services and data are active and must be preserved. Do not stop ingestion, relocate storage, re-ingest documents, regenerate embeddings, or run destructive experiments against the POC.

If existing data is needed, define a deliberate migration using a supported API or explicit export/import contract. Data migration is separate from implementing Ensemble's capabilities.

## 8. Providers and adapters

Embedding, reranking, and optional generation providers are pluggable behind Ensemble-owned contracts. Provider-specific behavior must not leak into public API contracts.

MCP is an optional adapter over existing Ensemble REST operations. REST remains canonical; MCP tools map to existing operations; MCP contains no duplicate business logic; and Ensemble remains fully usable when MCP is disabled.

## 9. Explicitly out of scope initially

- The broader FlossWare ecosystem and its shared infrastructure implementations.
- Client-specific workflows inside the Ensemble platform.
- A mandatory MCP server.
- Federation and distributed service discovery.
- Service mesh, message brokers, and distributed coordination.
- A required generation model.
- Runtime dependency on either historical Claude Ensemble repository.

## 10. Implementation sequence

1. Establish repository conventions and REST contract standards.
2. Inventory every POC capability, endpoint, behavior, test, dependency, and operational assumption without disturbing active services or data.
3. Define parity acceptance criteria and map each capability to an Ensemble contract and implementation.
4. Implement the complete capability surface inside the one-app/one-REST-server architecture.
5. Deliver the Control Center web interface against the same REST contracts.
6. Validate behavior and performance with the independent Evaluation project.
7. Add partition enforcement and client integrations without changing canonical contracts.

## 11. Architectural acceptance test

A capability can move from the initial co-located deployment to a separate process or host without changing its REST contract or consumer behavior.

The platform is independently usable without an AI client, MCP, or generation provider. Capability parity is demonstrated by contract-level regression tests against the POC's documented behavior, not by matching its process layout.
