# Ensemble Architecture

**Status:** Accepted architectural direction. Federation and the four public interfaces are required capabilities; implementation status is tracked separately in docs/implementation-status.md and docs/review-readiness.md.

## 1. Purpose and product boundary

Ensemble is a standalone platform for reusable intelligence capabilities, including Memory, Graph, Knowledge, ingestion, indexing, retrieval, model access, embeddings, ranking, and context assembly.

Ensemble owns the shared platform and its canonical contracts. Client-specific integrations belong in separate consumer repositories and are not part of Ensemble's implementation or documentation scope.

Ensemble must operate independently of any client application and optional model provider. Model access is a capability owned by Ensemble, not a client integration. Historical repositories may provide narrowly scoped implementation evidence when needed, but they are not the product definition, architecture template, or implementation target. Ensemble's scope is determined by its own requirements and contracts.

## 2. Deployment shape

The initial deployment remains deliberately simple within each instance: one application, one local persistence boundary, and independently addressable capabilities behind stable contracts. An Ensemble instance may run on a workstation, laptop, or server. Every instance owns its own orchestration authority, governed state, policies, services, workers, and Control Center, and remains operational without network access.

Federation is now an explicit product requirement, not an optional infrastructure experiment. Independently operated instances may discover and cooperate with explicitly trusted peers without a global coordinator, shared database, mandatory broker, service mesh, or distributed consensus. See [Federation](federation.md) for the peer contract, discovery, presence, trust, transport, delegation, messaging, and artifact exchange requirements.

## 3. REST is the canonical boundary

REST/HTTP is the canonical resource/operation contract for external consumers and intra-instance capability communication. A capability calls another co-located capability through its REST contract, even when both share a process. Direct in-process calls are not the normal service boundary. Co-location is a deployment choice, not permission to bypass the contract.

Across independently deployed Ensemble peers, the versioned gRPC federation contract is the preferred typed service-to-service transport; REST remains available for resource-oriented peer operations and administration. MCP and GraphQL are additional consumer interfaces. Every interface delegates to the same application/domain services and preserves equivalent authorization, partition, policy, provenance, validation, and error behavior.

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

## 8. Model access, providers, and adapters

Model access is a first-class Ensemble capability exposed through provider-neutral REST contracts. Provider-specific protocols and credentials remain behind adapters; provider details must not leak into the public contract.

EnMaaS is the identified enterprise model-gateway integration direction. It is an integration target, not a dependency required to run Ensemble. Model identity, provider availability, and capability support must be explicit. Do not silently switch providers or models when a requested provider is unavailable.

Embedding and ranking providers are pluggable behind Ensemble-owned contracts. Optional generation is provider-backed and must not become a dependency of retrieval or other non-generation capabilities.

MCP, GraphQL, and gRPC are required first-class interfaces over the same application/domain services as REST. REST is the canonical resource/operation contract. MCP exposes tools/resources to AI clients; GraphQL supports bounded client-driven queries/mutations; gRPC provides typed service-to-service operations and justified streaming. These interfaces must share authorization, partition enforcement, provenance, validation, error semantics, and persistence. None may create duplicate business logic or a separate persistence path.

## 9. Explicitly out of scope

- The broader FlossWare ecosystem and its shared infrastructure implementations.
- Client-specific workflows and AI-client runtimes inside the Ensemble platform.
- Mandatory service mesh, external message broker, global coordinator, distributed consensus, or shared database.
- A required generation model.
- Premature provider federation or a model broker.
- Rebuilding or migrating unrelated active data stores as a prerequisite for Ensemble implementation.

Federation, peer discovery/presence, messaging, service/task delegation, federated RAG, and storage-independent derived-artifact exchange are required. They are not out of scope; see [Federation](federation.md).

## 10. Implementation sequence

1. Finish the architecture and foundational REST contracts.
2. Inventory capabilities and dependencies only as needed to implement the agreed contracts.
3. Establish the application and REST server skeleton.
4. Consolidate capabilities incrementally, including Memory, Graph, Knowledge, ingestion, indexing, embeddings, retrieval/RAG, and context assembly.
5. Integrate the relevant deletion lifecycle within each capability: soft deletion, restoration, purge strategies, and propagation to derived data.
6. Validate data integrity, failure behavior, recovery, and operational behavior.
7. Deliver the required MCP, GraphQL, and gRPC interfaces over the shared core services.
8. Deliver federation incrementally: peer identity/trust, automatic discovery/presence, capability negotiation, reliable peer transport, messaging, service/task delegation, federated RAG, and derived-artifact exchange.
9. Build client integrations as independent consumers in separate ensemble-* projects.
10. Revisit generic contracts and broader abstractions only when concrete use cases justify them.

Do not create a separate phase or framework for a concern that belongs inside the capability being implemented.

## 11. Architectural acceptance tests

- Ensemble starts and serves its REST APIs without any AI client installed or running.
- Retrieval works when no generation model is configured or reachable.
- A capability can move from the co-located deployment to another process or host without changing its REST contract or consumer behavior.
- Internal capability calls use the same REST contracts as external calls.
- REST, MCP, GraphQL, and gRPC exercise the same core services and enforce equivalent authorization and partition isolation.
- Each instance remains operational without peers; discovery/presence is automatic by default when federation is enabled.
- Discovery announcements never establish trust; only mutually authenticated and authorized peers can invoke protected capabilities.
- UDP is used only for best-effort discovery/presence; reliable operations use acknowledged transports.
- Federation, messaging, delegation, federated RAG, and artifact exchange recover safely from disconnects and restarts without shared database files or global coordination.
- Data deletion, restoration, and permanent purge cannot leave stale derived data visible or allow deleted data to be resurrected.


## 12. Test Harness

Ensemble includes a Test Harness contract for synthetic injection, deterministic replay, and controlled fault scenarios through the canonical REST API. It is disabled by default and restricted to a dedicated test deployment and database until end-to-end partition enforcement exists. The harness must use existing ingestion and deletion contracts, retain per-operation outcomes, and never write directly to persistence tables. See [Test Harness](test-harness.md) for its lifecycle, route family, safety requirements, and implementation sequence.

## Data partitions

Ensemble uses **physical SQLite database isolation** for data partitions. The existing configured database remains the `default` partition. Each named partition receives a separate SQLite file beneath the configured database directory at `partitions/<sha256(partition-id)>.db`; the raw partition identifier is not used as a filesystem path. This keeps source identifiers, memory names, graph identities, vector collections, indexes, transcript checkpoints, learning state, and generation metadata independent without relying on query-time filters.

The REST boundary derives the principal and authorized partition set from a Bearer credential. Configure `ENSEMBLE_PARTITION_TOKENS` as a JSON object keyed by the lowercase SHA-256 digest of each token. Each value contains a `subject` and a non-empty `partitions` array. The optional `X-Ensemble-Partition` header selects among those authorized partitions; it never grants access by itself. If a principal has one authorized partition, it is selected automatically. If the token map is configured, API requests require a valid Bearer token. Set `ENSEMBLE_AUTH_REQUIRED=1` to fail closed when authentication is required but no token map is configured.

When no token map is configured, local single-partition mode is limited to `default`; a caller cannot select another partition by supplying a header. The public liveness/readiness and documentation routes remain available without a token. Keep the listener on loopback in this mode.

All SQLite-backed capabilities resolve their database connection from the authenticated request scope. Internal REST calls propagate the selected partition and credential. A new named partition is provisioned by applying the same Alembic migration chain to its empty database on first use, then recording and checking the partition identity. Existing default-partition data is not copied into named partitions.

The in-memory adapters are test/development adapters and are not a multi-partition persistence option. Do not use them for a deployment that enables partition credentials.


## 13. Federation

Every Ensemble instance is an autonomous peer. When federation is enabled, it listens for peer announcements and broadcasts minimal presence by default: promptly at startup, periodically (initial design default 30 seconds), after endpoint/capability changes, and on clean shutdown. A configurable TTL (initial design default three heartbeat intervals) expires peers that disappear unexpectedly. mDNS/DNS-SD and scoped UDP multicast support LAN discovery; static peers, DNS, and optional rendezvous/peer exchange support routed networks. Announcements are untrusted hints and contain no credentials or user/task data. Mutual authentication, protocol negotiation, trust validation, and local authorization are required before peer use. UDP is not a reliable task or messaging transport. SSH remains the constrained remote-worker transport; authenticated gRPC is the preferred peer transport. See [Federation](federation.md) for the complete contract and acceptance criteria.
