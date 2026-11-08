# Ensemble Federation

**Status:** LAN discovery, heartbeat presence, live peer inspection, and Control Center visibility are implemented. Delegated task execution now dispatches through the core per-instance Orchestrator and registered task handlers. Task execution and persistence now use transport-neutral `TaskRequest`, `TaskSubmission`, `TaskRecord`, `Orchestrator`, and `SqliteTaskStore` contracts. Startup migrates the old federation-named database and table. Trust establishment and artifact exchange remain incomplete. Federation does not require a general-purpose reliable messaging subsystem; delegated tasks are persisted by the receiving instance and their status/results are queried from that instance when reachable. See [Orchestration](orchestration.md).
**Scope:** Discovery, presence, trust establishment, direct peer communication, delegation, and exchange between independently operated Ensemble instances.

## 1. Purpose and operating model

Every Ensemble deployment is an autonomous platform and a potential federation peer. An instance owns its own orchestration authority, policies, governed data, persistence, capabilities, workers, and Control Center. It remains useful when no peers are reachable and continues local work while disconnected.

Federation connects independently governed instances. It does not create a global coordinator, shared database, global leader, or implicit trust domain. Each instance retains authority over its own tasks, data, resources, and policy decisions.

An instance may run on a workstation, laptop, home server, or remote server. Deployment role does not change the peer contract. A peer may offer services, accept bounded delegated tasks, publish authorized derived artifacts, consume artifacts, or do several of these.

Ensemble is the platform and orchestration authority, not an AI client. Client-specific runtimes and interaction loops remain in separate `ensemble-*` projects.

## 2. Discovery and automatic presence

### Implemented LAN discovery slice

The current application broadcasts a small JSON presence announcement over IPv4 multicast by default, listens on the same group, ignores its own instance ID, and maintains an in-memory peer registry. The Control Center polls `/federation/status` and renders discovered peers, capabilities, endpoints, last-seen age, and online/offline status. Expired peers remain visible as offline for a bounded retention period before being pruned. This is discovery only: it does not establish peer trust. The current `search.query` operation is the first registered orchestrator handler. Federation submits accepted peer requests to the core Orchestrator; the API no longer owns a dedicated delegated-task worker loop. Federation maps its peer envelope into the shared task contract; the legacy `ensemble.federated_tasks` module remains only as compatibility aliases.

Current defaults: multicast group `239.255.76.76`, UDP port `47676`, heartbeat 30 seconds, and presence TTL 90 seconds. Configure with `ENSEMBLE_FEDERATION_ENABLED`, `ENSEMBLE_FEDERATION_GROUP`, `ENSEMBLE_FEDERATION_PORT`, `ENSEMBLE_FEDERATION_HEARTBEAT_SECONDS`, and `ENSEMBLE_FEDERATION_TTL_SECONDS`. The UDP group and port are configurable; multicast is link-local and best effort.

When federation is enabled, every running instance MUST both listen for peer announcements and broadcast its own presence by default.

Announcement lifecycle:

- On startup, announce promptly after the instance has initialized its identity and reachable endpoints.
- Send a configurable periodic heartbeat while running. The initial design default is 30 seconds.
- Announce promptly after a network-interface, endpoint, protocol, capability, or availability change.
- Send a departure announcement on clean shutdown.
- Expire a peer after a configurable presence TTL if valid announcements stop arriving. The initial design default is 3 heartbeat intervals.
- On network recovery or restart, announce immediately and re-establish peer sessions.

The 30-second heartbeat and three-interval TTL are initial defaults, not hard-coded protocol constants. Implementations must tolerate jitter, packet loss, duplicate and out-of-order announcements, and temporary network partitions.

### 2.1 Discovery transports

Use simple, standard mechanisms first:

- **mDNS/DNS-SD** for ordinary LAN service discovery.
- **Scoped UDP multicast** for lightweight Ensemble-specific presence and capability-change announcements where useful.
- **Static peer configuration** for explicitly configured peers and deterministic deployments.
- **DNS SRV or equivalent DNS records** for managed networks.
- **Optional rendezvous or peer exchange** for networks where multicast does not cross routers.

LAN multicast is link-local in normal deployments and must not be assumed to traverse routers, VLANs, VPNs, or the public Internet. Discovery scope must be configurable. IPv4 and IPv6 behavior should be documented and tested separately.

UDP is best-effort discovery/presence transport only. It is not the delivery mechanism for tasks, commands, service requests, or durable events.

### 2.2 Announcement contents

Announcements are small, versioned, non-sensitive discovery hints. They may contain:

- Stable instance ID.
- Instance display name.
- Announcement/schema version and supported protocol versions.
- Reachable endpoint addresses and supported protocols.
- Capability identifiers and capability-contract versions.
- Availability state.
- Monotonic announcement sequence or equivalent freshness metadata.
- Issued-at time and expiry/TTL information.

Do not broadcast credentials, bearer tokens, private keys, certificates, task content, user data, source content, database details, or sensitive internal state. Detailed capability metadata is exchanged only after an authenticated session is established and authorized.

Treat every announcement as untrusted input. Validate size, schema, version, address handling, freshness, rate, and resource use. An announcement never establishes trust or grants access.

## 3. Peer identity and trust handshake

Discovery produces candidate peers, not trusted peers. Before invoking a peer capability or exchanging protected data, the instances MUST complete a mutually authenticated handshake.

The handshake validates:

1. Stable peer identity and proof of private-key possession.
2. Certificate or equivalent trust-chain validity and configured trust policy.
3. Peer endpoint and protocol compatibility.
4. Supported contract and schema versions.
5. Requested relationship and capability scopes.
6. Local authorization and policy approval.
7. Resource limits, delegation constraints, and applicable data partitions.

Trust is explicit and revocable. Unknown peers are not automatically trusted merely because they are on the same LAN, share a VPN, or announce a familiar name. Revocation must prevent new work and define how in-flight work and retained artifacts are handled.

The exact certificate provisioning, rotation, and revocation mechanism remains an implementation decision. Do not invent a custom cryptographic protocol; use established mutual-TLS and certificate-validation libraries and document the chosen trust lifecycle.

## 4. Peer communication and interfaces

The peer transport is separate from discovery:

- **gRPC over authenticated TLS** is the initial preferred peer-to-peer transport for typed service calls and task operations. Use streaming only where it materially improves a specific operation; it is not a durable messaging guarantee.
- **REST/HTTP** remains available for resource-oriented operations, administration, diagnostics, and compatibility.
- **SSH** is the standard transport for constrained remote execution workers. SSH is not the federation protocol, peer discovery mechanism, or authorization model.
- MCP and GraphQL remain supported external interfaces to the same core application/domain services; they do not create separate federation business logic.

Transport adapters must delegate to the same application/domain contracts and enforce equivalent identity, authorization, partition, policy, provenance, deadline, cancellation, and error semantics.

## 5. Federation responsibilities

### 5.1 Service delegation

A requester may invoke a specific versioned service on an authorized peer while retaining ownership of its parent task. The provider owns the execution lifecycle of the service request. Contracts include typed inputs/outputs, idempotency, correlation, deadlines, cancellation, structured errors, usage metadata, bounded retries for safe operations, circuit breakers, fallback, provenance, and hop/depth limits.

### 5.2 Task delegation

A requester may assign a bounded task to another Ensemble. The receiving peer owns planning and execution of that delegated task under its own policies. The requester retains ownership of the parent task and overall outcome. Preserve task identity and lineage, scope, policy constraints, checkpoints, evidence, artifacts, status, cancellation, recovery, and result provenance. The receiving instance persists its task lifecycle; the requester queries that instance for status and results when reachable. A lost response is an ambiguous outcome, not proof that execution failed.

#### Current experimental delegation spike

The branch currently contains a narrow end-to-end `search.query` proof of concept:
- A peer can submit a search request to another discovered peer.
- The receiving API maps it into the canonical per-instance `SqliteTaskStore`.
- The core Orchestrator claims the request, invokes the registered local search handler, and records a result.
- The Control Center can inspect recent delegated task records.

This proves a basic peer-to-peer request path. The core Orchestrator now owns worker scheduling and handler dispatch, and the FastAPI lifecycle starts/stops the orchestrator as a service. Task persistence now uses the transport-neutral `SqliteTaskStore` and canonical task models. Existing federation-named database files and tables are migrated to the canonical task store on startup. Do not reintroduce worker loops into federation or API routes.

Requester-side parent/child references are now persisted before outbound peer submission, including an explicit `submitted` state for ambiguous outcomes. Policy enforcement, richer execution controls, trust establishment, and artifact exchange remain incomplete. Federation must remain a thin adapter. The requester can query the provider's persisted task resource for status and results; there is no requirement for guaranteed push delivery or a separate messaging subsystem.

### 5.3 Federated RAG

An instance may retrieve from authorized peers. Source-level authorization and partition filtering must occur before remote candidates can influence local fusion, reranking, or context assembly. Preserve source identity, revision, provider instance, provenance, and evidence. Remote unavailability must have explicit degraded behavior.

### 5.4 Communication and task status

Federation does not require a general-purpose messaging subsystem, durable message queues, publish/subscribe, dead-letter handling, or guaranteed event delivery. Use direct authenticated service/task requests over the peer transport. The receiving instance persists accepted tasks in its own task store and exposes task status and results through its task API. The requester may poll while the peer is reachable. If a request times out or its response is lost, the requester uses the same task identity/idempotency key when checking or resubmitting; it must not assume the remote operation failed.

UDP remains limited to best-effort discovery and presence announcements. It must not carry task payloads or commands.

### 5.5 Derived-data and artifact exchange

Instances exchange reusable computed artifacts, not database files or storage-engine internals. Examples include chunks, embeddings, vectors, lexical-index artifacts, graph nodes/edges, extracted relationships, knowledge records, and retrieval/reranking outputs.

Artifacts use versioned, storage-independent manifests containing stable artifact/source IDs, producer identity, artifact/schema type, source revision, computation and dependency identity, model/provider/revision, dimensions/normalization where relevant, parameters, timestamps, integrity hashes, provenance, authorization scope, and lineage.

Support snapshots and incremental feeds, content deduplication, bounded cursors, acknowledgements/checkpoints, idempotent import, integrity validation, retry/cancellation, backpressure, and restart/reconnect recovery. Consumers persist through their own storage adapters. Compatibility is explicit: never silently mix incompatible embedding models, dimensions, chunking profiles, analyzers, or extraction revisions. Incompatible or stale artifacts are retained separately, rejected, or explicitly recomputed. Invalidation, supersession, and artifact-specific conflict rules are required.

Source content and derived artifacts are separately governed. Local-only data remains local unless explicitly authorized for export. No live database-file copying, shared database, or global consensus.

## 6. Failure and recovery model

Each instance remains independently operational without network access. Federation must tolerate:

- Peer disappearance, network partitions, packet loss, and delayed/duplicate announcements.
- Peer restart, address changes, capability changes, and protocol-version mismatch.
- Request timeout, cancellation, partial failure, and provider overload.
- Duplicate requests and repeated artifact delivery.
- Revoked trust and changed authorization.
- Interrupted transfers and consumer restart.
- Stale, incompatible, conflicting, or integrity-invalid artifacts.

Use bounded retries with backoff, idempotency keys, deadlines, checkpoints, durable local state where required, circuit breakers, and explicit partial/unavailable results. Never retry indefinitely or allow a remote peer to amplify work without bounds.

## 7. Control Center

The Control Center is the operator interface to federation, not an AI client. Its federation workbench should expose:

- Discovered candidates separately from trusted peers.
- Peer identity, trust status, endpoint, protocol compatibility, and last-seen/expiry.
- Availability and capability inventory with version information.
- Active service and task delegations, progress, deadlines, and outcomes.
- Artifact-transfer status, checkpoints, retries, and failures.
- Revocation and explicit trust-management operations with confirmation.
- Audit/provenance links and clear disconnected/degraded states.

The browser remains a consumer of Ensemble REST contracts. It must not connect directly to peer databases or bypass platform authorization.

## 8. Initial implementation choices

Prefer Python-native components and avoid mandatory infrastructure:

- Python `asyncio` and standard `socket` support for UDP discovery and presence.
- `zeroconf` for mDNS/DNS-SD.
- `grpcio` and `grpcio-tools` for typed peer communication.
- Existing FastAPI contracts for REST.
- `cryptography` and established TLS facilities for peer identity and trust.
- SQLite per instance for local peer registry, trust metadata, task state, and transfer checkpoints where appropriate.

Do not introduce ZooKeeper, a global database, mandatory Kafka/RabbitMQ, a service mesh, or distributed consensus for the initial federation implementation. A peer must be fully useful without any of these systems.

Suggested package boundaries (subject to existing repository conventions):

```text
ensemble/
  federation/
    discovery.py
    announcements.py
    peers.py
    identity.py
    trust.py
    health.py
    capabilities.py
    transport.py
```

The package sketch is a design aid, not a claim that these modules already exist.

## 9. Configuration principles

Configuration must make federation behavior explicit and safe. The eventual configuration contract should cover:

- Federation enabled/disabled.
- Instance identity and advertised endpoints.
- Discovery transports, interfaces, multicast scope, and announcement interval.
- Presence TTL and peer expiry.
- Static peers and optional DNS/rendezvous sources.
- Trust roots, local identity, certificate rotation, and revocation.
- Allowed peer identities, capabilities, partitions, and delegation scopes.
- Resource, transfer, retry, and hop limits.

Defaults: federation enabled for peer discovery/presence in a federation-capable deployment; minimal non-sensitive announcements; no automatic trust; no automatic data sharing; no unrestricted delegation. Operators may disable or scope discovery explicitly.

This section defines intended configuration, not current environment-variable names or a working configuration surface.

## 10. Acceptance criteria

Federation is not complete until automated and multi-process tests prove:

1. Instances independently start, stop, and perform local work without peers.
2. Startup, periodic, change, and departure announcements work; TTL expiry detects unexpected disappearance.
3. mDNS/DNS-SD and scoped UDP discovery work on supported LAN configurations; static/DNS configuration works where multicast is unavailable.
4. Duplicate, malformed, stale, oversized, and spoofed announcements are safely rejected or rate-limited.
5. Discovery never grants trust; mutual authentication, authorization, protocol negotiation, and revocation are enforced.
6. Untrusted peers cannot enumerate protected capabilities or invoke services.
7. Service delegation preserves requester parent-task ownership and provider request ownership.
8. Task delegation preserves task lineage, bounded authority, cancellation, recovery, and provenance.
9. Federated RAG enforces source authorization and partition isolation before fusion/reranking.
11. Artifact exchange proves snapshot and incremental transfer, checkpoint recovery, deduplication, integrity, compatibility, invalidation, conflict handling, and import through different local storage adapters without database-file copying.
12. Disconnect/reconnect, peer restart, partial failure, timeout, cancellation, duplicate requests, ambiguous timeouts, and revocation are exercised across independently running instances.
13. Control Center reports discovered/trusted/unavailable peers and active delegations from real API data, including explicit degraded states.
14. Audit records identify the acting instance, peer, task/request, capability, authorization decision, data scope, artifact provenance, and outcome without recording secrets.

Until these tests pass, federation remains a required but incomplete capability.
