# Ensemble Orchestration

**Status:** Initial core Orchestrator and handler dispatch are implemented. The first registered handlers are `search.query` and `ga.run`. Both local `POST /api/v1/tasks` and federated `POST /federation/tasks` submissions now enter the same Orchestrator and handler dispatch path. The canonical `TaskRequest`, `TaskSubmission`, `TaskRecord`, and `SqliteTaskStore` now replace federation-specific types in the runtime. The store uses `tasks.sqlite3` and migrates the former `federated-tasks.sqlite3` database/table in place. Existing GA runs and transcript jobs retain their domain-specific lifecycle stores.

## 1. Ownership

Orchestration is a core Ensemble capability. It owns the lifecycle of work regardless of whether work was submitted locally, through REST/MCP/GraphQL/gRPC, or by a federation peer.

The orchestrator is the single authority for:
- Task identity, parent/child lineage, correlation and causation.
- Admission, validation, policy evaluation, scheduling and dispatch.
- Execution state, progress, checkpoints, deadlines and cancellation.
- Retry policy, idempotency, recovery, timeout and terminal outcomes.
- Worker assignment, capability matching, resource limits and backpressure.
- Result, artifact and evidence provenance.
- Audit events and operator-visible lifecycle history.

A transport adapter must not implement a second task state machine or worker loop.

## 2. Federation boundary

Federation is peer discovery, communication and delegation transport. It is not an execution engine.

For inbound delegation:
1. Federation receives and validates the peer request envelope.
2. The adapter maps the envelope to the canonical orchestration submission contract, preserving origin instance, remote task ID, correlation/causation, deadline, requested capability and delegation constraints.
3. The orchestrator admits or rejects the task under local policy.
4. The orchestrator schedules and dispatches work to registered local capability handlers/workers.
5. The adapter publishes accepted, progress, terminal and result events back to the requesting peer using reliable messaging.
6. The provider's orchestrator remains authoritative for the delegated execution. The requester retains authority over its parent task.

For outbound delegation, the local orchestrator creates and owns the parent task and delegated-child relationship. Federation sends the child request and delivers remote lifecycle updates. A network timeout must not be confused with remote task failure; the remote task may have been accepted even when the response was lost.

Discovery announcements are hints, not authorization. A peer's advertised capability is not proof that a particular task is permitted or executable.

## 3. Canonical task contract

The core contract should be transport-neutral and versioned. At minimum it carries:
- Stable task ID and idempotency key.
- Task type and contract version.
- Origin/requesting instance and execution owner.
- Typed payload reference or bounded payload.
- Parent task, correlation ID and causation ID.
- Created/accepted timestamps, deadline and cancellation semantics.
- Required capability, resource constraints and policy/delegation scope.
- State, attempt, worker, progress and checkpoint references.
- Structured error or result/artifact references with provenance.

Keep task identity separate from delivery/message identity. Retries of message delivery must not create new tasks. Re-submitting the same task identity with different immutable request content is a conflict.

## 4. Core boundaries

- **Orchestrator:** lifecycle and policy authority.
- **Scheduler:** selects eligible work and workers; no business-specific task semantics.
- **Worker registry:** reports executable handlers, versions, capacity and health.
- **Execution handler:** implements one capability operation and returns structured progress/results.
- **Persistence:** stores canonical task state, events and checkpoints per instance.
- **Messaging:** reliable transport, acknowledgements, delivery retries and subscriptions. It does not own task state.
- **Federation adapter:** maps peer protocol envelopes/events to and from the orchestrator.
- **Control Center:** reads orchestrator APIs and issues authorized operator commands. It does not inspect task databases directly.

All client and transport interfaces invoke the same application services. Do not duplicate scheduling, policy, execution, or persistence in REST, MCP, GraphQL, gRPC, or federation adapters.

## 5. Existing implementation assessment

The current code has useful domain-specific lifecycle foundations:
- GA runs persist their domain-specific result and promotion data in `GAStore`; execution is now scheduled by the core Orchestrator through the `ga.run` handler.
- Transcript ingestion jobs persist attempts, status, results and interrupted-job recovery in `TranscriptCheckpointStore`.
- Generation and indexing workflows have their own durable lifecycle and recovery rules.
- The initial federation implementation used `FederatedTaskStore` and an API-owned worker loop. The worker loop has been moved into the core `Orchestrator`, which owns handler registration, scheduling, dispatch, execution, and worker lifecycle. `SqliteTaskStore` now owns canonical task persistence in `tasks.sqlite3`; startup migrates the former federation-specific database and table. The old `ensemble.federated_tasks` module remains only as compatibility aliases.

These are not yet one fully coherent orchestration system. GA and transcript lifecycles remain domain-specific and must be mapped deliberately, preserving their recovery semantics.

## 6. Migration sequence

1. Add task schema versioning, partition-aware persistence, and migration/rollback coverage for the canonical task store.
2. Inventory and map existing GA, transcript, generation and indexing lifecycle behavior. Preserve domain-specific state and recovery semantics; do not blindly replace mature stores with a generic blob.
3. Add explicit task events, progress, cancellation, deadlines, bounded retry policy, and parent/child lineage to the canonical lifecycle.
4. Adapt transcript ingestion through a registered handler where appropriate, retaining domain-specific checkpoint stores for their own work units. GA execution already uses the `ga.run` handler and retains `GAStore` for domain-specific results.
5. Add requester-side parent/child tracking and reliable lifecycle/result delivery over messaging.
6. Move Control Center task views to canonical orchestrator APIs.
7. Add further handlers only after the shared lifecycle works end to end.

## 7. Acceptance criteria

- Local and federated submissions use the same orchestrator and task state machine.
- No API route or federation adapter starts its own execution loop.
- The orchestrator works without peers and survives peer/network loss.
- A remote delivery retry cannot duplicate task execution.
- Parent/child lineage and provider/requester ownership are explicit.
- Cancellation, deadlines, bounded retry, restart recovery and terminal results have consistent semantics.
- GA, transcript, generation and indexing workflows have documented integration boundaries and no accidental loss of domain-specific recovery.
- Control Center shows canonical task state and provenance from the orchestrator API.
