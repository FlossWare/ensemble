# POC-to-Ensemble Functional Parity Audit

**Audit snapshot:** 2026-11-08  
**Reference branch:** `feature/ledger-pull-parity`  
**POC HEAD:** `388d81977e6c72a18d1b6c814ad50f7e510b106c`  
**Ensemble HEAD:** `67cc45dacd2033bfa01b1dc59642e7ced8e59233`  
**Status:** Initial source inventory and contract review. Runtime parity scenarios have not yet been executed.

## Executive assessment

Ensemble already contains substantial counterparts for the POC's data and intelligence services. It is not yet demonstrated to be functionally equivalent across the POC's observable workflows.

The current evidence supports **broad capability coverage, with material partial-parity gaps**, not a defensible overall completion percentage. The ledger is one subsystem and its latest CI does not establish live cross-process transfer: the optional live transfer test remains skipped unless both service URLs are configured.

This audit deliberately distinguishes:
- **Capability presence:** a corresponding module or endpoint exists.
- **Behavioral parity:** observable behavior and edge cases match the POC contract.
- **Operational parity:** restart, failure, recovery, migration, diagnostics, and real deployment behavior are proven.
- **Scope:** whether a POC subsystem is part of Ensemble's intended successor contract.

A module or UI panel alone is not proof of parity.

## Normative successor requirements

The following are **mandatory Ensemble product capabilities**. They are not optional follow-up integrations, stretch goals, or scope decisions:

1. **RAG is required.** Ensemble must provide an end-to-end retrieval-augmented generation capability over its governed knowledge and memory sources. This includes ingestion and indexing, lexical and vector retrieval, hybrid fusion, optional graph/knowledge expansion, reranking, authorization and partition filtering before candidates can influence fusion or reranking, provenance-preserving context assembly, and explicit degraded/failure behavior. The capability must be callable through stable client-neutral contracts.
2. **MCP server support is required.** Ensemble itself must expose its client-neutral capabilities through an MCP server interface, in addition to REST. MCP tools/resources must delegate to the same core contracts and enforce the same authorization, validation, provenance, and error semantics. MCP is an external interface/adapter, not a dependency required by Ensemble's internal implementation.
3. **GraphQL API support is required.** Ensemble must expose a first-class GraphQL API for client-driven queries and mutations over its client-neutral domain contracts. GraphQL resolvers must delegate to the same application/domain services as REST and MCP, enforce equivalent authorization and partition isolation, bound query depth/complexity and resource use, and provide stable schema/versioning and error semantics. GraphQL must not create a parallel business-logic or persistence implementation.
4. **Claude Code integration is separate.** Claude Code hooks, Claude-specific configuration, session lifecycle, and Claude-specific workflows/adapters belong in `ensemble-claude`. This separation does not weaken or remove Ensemble's mandatory MCP or RAG requirements.
5. **Development and automation capabilities are required.** Ensemble must support authorized software-development work as a client-neutral platform capability, not as Claude Code-specific behavior. This includes repository inspection, local source-file operations, multi-file code changes, build/test/diagnostic execution, Git workflows, and reviewable change sets.
6. **SSH is the standard remote transport.** Remote development machines and workers are reached over SSH using authenticated, host-verified connections. SSH is the transport, not the authorization model: a constrained worker/execution service on each remote host must enforce approved workspaces, allowed operations, command policies, resource limits, and audit capture. Do not expose unrestricted shell access as the default capability. Local execution must not be assumed available merely because the Ensemble server is remote.
7. **GitHub integration is required.** Ensemble must integrate with GitHub through authenticated GitHub APIs for repository, branch, commit, issue, pull-request, review, and CI/workflow operations. Git operations performed on a remote worker must remain attributable and produce reviewable diffs and provenance. External or destructive actions (for example, pushing changes, creating or modifying pull requests, merging, deleting branches, or changing repository settings) require explicit policy authorization and, where configured, human approval.
8. **Engineering workflows must be auditable and recoverable.** Ensemble must support planning and executing bounded development tasks, running builds/tests, diagnosing failures, tracking checkpoints and outcomes, and recovering or resuming interrupted work. Every action must retain actor/task/workspace identity, target host/repository, command or API operation, timestamps, result, and relevant artifacts without logging secrets.
9. **Ensemble is the orchestration authority.** Ensemble itself owns goal decomposition, planning, dependency management, task scheduling, delegation, worker/tool selection, coordination, retries, cancellation, checkpoints, recovery, policy/approval enforcement, and result synthesis. SSH workers, local workers, GitHub, model providers, and other integrations are execution or service adapters. They execute bounded assignments and report evidence; they do not contain competing orchestration engines or independently redefine task state. Orchestration state and lifecycle must be durable and observable through the shared core services and exposed consistently through REST, MCP, and GraphQL.


**Acceptance rule:** Ensemble cannot be declared functionally complete or at parity while the RAG end-to-end acceptance suite, MCP server contract suite, or GraphQL schema/resolver contract suite is missing, failing, or unrun. The local workspace, SSH worker, Git/GitHub, and engineering workflow acceptance suites are also mandatory. They must exercise actual authorized workspaces and remote hosts, enforce host verification and least privilege, prove that unauthorized paths/commands/actions are rejected, and verify complete audit/provenance records and recovery behavior. Source files, placeholder endpoints, UI previews, and architecture statements do not satisfy these gates. All three API interfaces must be exercised against the real core capability implementations, including authorization/partition isolation, persistence/restart, error/degraded behavior, and representative end-to-end scenarios.

## Repository inventory

| Measure | POC | Ensemble | Interpretation |
|---|---:|---:|---|
| Tracked files | 673 | 131 | POC includes a much broader repository surface |
| Test files | 156 | 31 | Counts include different scopes and are not directly comparable |
| Backend shape | Multiple loopback REST services plus CE core | Consolidated FastAPI application with internal capability contracts | Different implementation shape is acceptable if contracts match |
| Persistence | Multiple service-specific stores, ChromaDB vector persistence, JSON/file state | SQLite-backed stores and migrations | Expected technology difference; behavior must be compared |
| Control Center | Existing tools/dashboards and service diagnostics | Browser UI served by Ensemble | UI coverage exists, but several diagnostics remain incomplete |

POC's `services/` directory includes memory, graph, knowledge, index, embeddings, ranking/reranking, chunking, tokenization, extraction, intent, learning, Thompson, arbitration, collaboration, execution, providers, workflows, scraping, and shared operational services. POC's `core/` also contains Claude Code hooks/configuration, compression, provider routing, reviewer MCP, session messaging, and other runtime integration features. The successor boundary is explicit: Ensemble is a client-neutral intelligence platform that must provide RAG and an MCP server interface as well as REST; `ensemble-claude` owns Claude Code-specific hooks, configuration, session lifecycle, and Claude-specific workflows/adapters.

Ensemble's 31 test files cover API, chunking, configuration, Control Center, embeddings, ingestion, GA, graph, learning, ledger, memory, partitioning, reclamation, reranking, scraping, diagnostics, Thompson, and transcripts. The file inventory confirms a real implementation, but does not prove all POC behavior has a counterpart.

## Capability parity matrix

Statuses:
- **PARTIAL:** corresponding capability exists, but parity or operational evidence is incomplete.
- **MISSING:** no corresponding implementation was found in this snapshot.
- **NOT_RUN:** a required comparative/runtime scenario has not been executed.
- **PASS:** equivalent behavior has been demonstrated by comparative tests. No broad subsystem currently receives PASS solely from file presence.

| Capability | POC evidence | Ensemble evidence | Status | Known gap / next proof |
|---|---|---|---|---|
| Memory CRUD, entries, recall | `services/memory/memory_service.py`, memory service tests, history APIs | `ensemble/memory_store.py`, memory routes, `tests/test_memory*.py` | PARTIAL | Compare names, append-once semantics, ordering, limits, recall ranking, event provenance, restart behavior, and compatibility. |
| Memory search (semantic / lexical / hybrid) | POC embeddings and hybrid service, memory retrieval tests | `/api/v1/memory/search/*`, `/api/v1/search/hybrid` | PARTIAL | Same fixtures and queries must be run against both; compare scores, ordering, degraded paths, and result metadata. |
| Graph | `services/graph-service/graph_service.py`, NetworkX MultiDiGraph, durable JSON persistence and HTTP tests | `ensemble/graph_store.py`, `graph_memory.py`, graph routes and tests | PARTIAL | Compare multiedges, stable IDs, traversal direction/depth, property filters, persistence/restart and mutation failure behavior. |
| Knowledge | `services/knowledge/knowledge_service.py`, revision/provenance/tombstone tests | `ensemble/knowledge_store.py`, Knowledge routes and tests | PARTIAL | Compare revision conflict rules, Index coordination, tombstones, provenance, duplicate behavior and restore/reclamation. |
| Vector storage | POC ChromaDB-backed `services/embeddings/vector_service.py`, exact vector query and tests | SQLite vector store and API routes | PARTIAL | POC's ChromaDB remains the reference implementation; compare dimensions/model identity, collection semantics, exact query scores, metadata, tombstones and pagination. Do not replace ChromaDB in POC. |
| Index/revision manifest | `services/index/index_service.py`, revision ordering and tombstones | `ensemble/index_store.py`, generation-aware SQLite index and tests | PARTIAL | Ensemble adds generation/rebuild semantics. Compare baseline revision decisions and tombstones separately from Ensemble-only extensions. |
| Chunking | POC versioned `window-v1`, `markdown-v1`, `message-v1` profiles and tests | `ensemble/chunking.py`, profile routes and tests | PARTIAL | Implemented profiles appear aligned in source; still require shared golden fixtures for boundaries, empty input, oversized sections, IDs and provenance. |
| Tokenization / lexical analysis | POC tokenization and lexical services | Ensemble tokenization and lexical search routes | PARTIAL | Compare analyzer inventory, normalization, offsets, identifiers, compound terms and FTS/BM25 behavior. |
| Embeddings | POC pinned Nomic model, ONNX/OpenVINO runtime selection and HTTP service | Ensemble provider abstraction, Nomic and OpenAI-compatible providers | PARTIAL | Compare exact model revision, prefixes, pooling, dimensions, normalization, backend selection, failure semantics and vector compatibility. |
| Reranking | POC dedicated cross-encoder/ranking services; optional rerank in hybrid flow | Ensemble reranker and ranking provider abstractions | PARTIAL | Compare model revision, candidate limits, score ordering, disabled/unavailable behavior and fallback. |
| RAG (retrieval-augmented generation) | POC memory/knowledge retrieval, lexical/vector/hybrid search, reranking, graph expansion and provenance | Ensemble memory/knowledge retrieval, lexical/vector/hybrid search, reranking, graph expansion and provenance | PARTIAL (REQUIRED) | Mandatory completion gate. Prove end-to-end retrieve-to-context behavior with shared corpora, source identity/revision/scope/provenance, authorization before fusion, reranking inputs, context assembly, degraded channels and end-to-end consumer tests. No parity sign-off until the suite passes. |
| File/URL/document ingestion | POC extraction, scraping, chunking, indexing and knowledge publication services | Ensemble file/URL/document ingestion routes | PARTIAL | Compare supported formats, limits, URL safety, extraction fidelity, provenance, revision behavior and failure cleanup. Ensemble URL fetch is explicitly single-page, not a crawler. |
| Transcript/session ingestion | POC transcript/session indexing and outcome capture | Ensemble transcript parsing, session ingestion and checkpoint store | PARTIAL | Compare source discovery versus caller-supplied payload, checkpoint/replay semantics, tool/subagent capture, idempotency and outcome recording. Ensemble docs note no durable job/checkpoint UI. |
| Thompson sampling | POC Thompson service and routing helpers | Ensemble Thompson algorithm/store/API and Control Center panel | PARTIAL | Compare arm/model state, priors, cost constraints, outcome updates, persistence, selection determinism/eligibility and task-aware routing. Current Ensemble docs explicitly note task/capability filtering is not implemented. |
| GA optimization | POC `core/ga_tuning` evaluators, scheduler, artifacts and dashboard | Ensemble GA implementation/store/API and Control Center panel | PARTIAL | Compare parameter schemas/bounds, evaluators, seed behavior, run lifecycle, fitness history, recovery, approval/export semantics. Ensemble evaluators are documented as synthetic/local and do not apply settings live. |
| Learning | POC learning service, autonomous outcomes, priors, portable artifacts, scoring and retry workflows | Ensemble learning store/API, outcomes and artifacts | PARTIAL | Compare scoring semantics, deduplication, retry/recovery, portable artifact compatibility, Thompson/Memory side effects and reset semantics. |
| Control Center | POC dashboards and service-specific diagnostics | Ensemble Control Center with platform, retrieval, memory, graph, chunking, pipeline, GA, Thompson and Learning panels | PARTIAL | Existing UI is meaningful but not complete. Mapping doc identifies missing independent channel comparison, embeddings/ranking panels, durable job history, metrics and recovery diagnostics. |
| Pipeline Inspector | POC pipeline scripts and per-service diagnostics | Ensemble preview inspector | PARTIAL | Current inspector is a sequential read-only preview of tokenization, entities, intent and chunking. It is not a correlated trace of actual ingestion/retrieval execution. |
| Persistence, migration, backup/restore | POC per-service SQLite/JSON/Chroma persistence and recovery scripts/docs | Ensemble SQLite-only persistence, migrations, backup/restore scripts and tests | PARTIAL | Compare upgrade from populated data, crash/restart recovery, backup consistency, restore validation and cross-version compatibility. |
| Append-only ledger | POC JSONL ledger and Ensemble SQLite ledger; shared contract and transfer harness | `services/memory/append_only_ledger.py`, `ensemble/ledger.py`, parity tests | PARTIAL | Latest CI is green, but live HTTP transfer remains skipped unless both URLs are configured. Strengthen source non-destructive proof and batch/retry/cursor/filter tests. |
| Diagnostics / health / configuration | POC health/readiness, service diagnostics and `ce diagnose` | Ensemble health/readiness, capability endpoints, config endpoint and search diagnostics | PARTIAL | Compare unavailable/degraded dependencies, configuration redaction, error taxonomy, operational metrics, startup and recovery. |
| Local workspace and code modification | POC execution/workflow capabilities provide repository and task execution building blocks | No complete authorized local-workspace execution bridge demonstrated in this snapshot | MISSING (REQUIRED) | Provide constrained workspace operations for listing/reading/creating/editing/deleting files, multi-file changes, diff generation, and safe path enforcement. Exercise against real local workspaces; test path traversal, symlink escapes, permissions, cancellation, partial failure and recovery. |
| Remote worker communication and execution | POC execution/workflow services provide remote/task execution patterns | No standardized SSH-backed remote worker contract demonstrated in this snapshot | MISSING (REQUIRED) | SSH is the required transport. Implement authenticated, host-key-verified connections to a constrained worker on each remote host. Define worker registration/capabilities, workspace authorization, command allow/deny policy, timeouts, resource limits, cancellation, result/artifact transfer, reconnect/recovery and audit records. SSH must not imply unrestricted shell access. |
| Git and repository operations | POC repository/execution workflows provide development-task integration | No complete end-to-end repository mutation workflow demonstrated in this snapshot | MISSING (REQUIRED) | Support status, diff, branch, commit and history operations through an authorized worker; isolate task changes in branches/worktrees where practical; preserve reviewable diffs and provenance. Test concurrent tasks, conflicts, interrupted operations and rollback/recovery. |
| GitHub integration | POC provider/workflow integrations include repository automation patterns | No complete authenticated GitHub integration demonstrated in this snapshot | MISSING (REQUIRED) | Use authenticated GitHub APIs for repositories, branches, commits, issues, pull requests, reviews and CI/workflow status. Enforce least-privilege scopes, explicit approval for external/destructive actions, webhook/API failure handling, rate-limit handling and auditable attribution. |
| Orchestration engine and task lifecycle | POC execution, collaboration, provider routing and workflow services | No complete client-neutral orchestration authority demonstrated in this snapshot | MISSING (REQUIRED) | Ensemble must own goal decomposition, plans, dependency graphs, scheduling, delegation, adapter/worker selection, task state, concurrency controls, cancellation, retries, checkpoints, recovery, policy/approval gates and result synthesis. Workers/adapters execute bounded tasks and return evidence; they must not own competing orchestration logic. Prove durable lifecycle behavior, observability and consistent access through REST, MCP and GraphQL. |
| Autonomous engineering workflows | POC execution, collaboration, provider routing and workflow services | No complete client-neutral engineering task lifecycle demonstrated in this snapshot | MISSING (REQUIRED) | Support bounded task planning, implementation, build/test execution, failure diagnosis, checkpointing, resumability and outcome capture under Ensemble orchestration. Demonstrate end-to-end tasks through REST, MCP and GraphQL without duplicating domain logic. Keep Claude Code-specific behavior in ensemble-claude. |
| REST contracts | POC gateway forwards to independent service contracts | Ensemble versioned REST API and internal REST client | PARTIAL | Shared consumer contract tests are needed for equivalent requests/responses and error behavior. |
| MCP server interface | POC core capabilities MCP and reviewer/code-search MCP interfaces | No equivalent first-class MCP server found in Ensemble tree in this snapshot | MISSING (REQUIRED) | Mandatory completion gate. Implement a first-class Ensemble MCP server exposing client-neutral core contracts alongside REST and GraphQL. Test tools/resources against the same implementations and verify authorization, validation, provenance, persistence, error and degraded semantics. MCP must remain an interface/adapter, not an internal dependency. No parity sign-off until the contract suite passes. |
| GraphQL API | No single POC GraphQL gateway identified in this source inventory; underlying client-neutral domain capabilities are exposed through service APIs | No equivalent first-class GraphQL schema/resolver layer found in Ensemble tree in this snapshot | MISSING (REQUIRED) | Mandatory Ensemble interface requirement. Define a stable GraphQL schema and resolvers over the same core application/domain services as REST and MCP. Test query and mutation behavior, authorization/partition isolation, pagination, error semantics, depth/complexity limits, and representative cross-domain queries. No duplicated business logic or persistence. No parity sign-off until the contract suite passes. |
| Provider routing, arbitration, collaboration, execution workflows | POC core/services contain provider registry, arbitration, collaboration, execution/recovery and workflow bridges | No corresponding first-class Ensemble modules found in current tree | MISSING* | Same scope issue. These are substantial POC capabilities and cannot be represented by GA/Thompson alone. |
| Compression | POC `core/compression` and compression APIs/hooks | No dedicated compression subsystem found in Ensemble tree | MISSING* | Determine whether this POC capability is required in the successor contract. |
| Alerts, messaging, cost tracking, experiments, policy | POC has alert service, session messaging, cost tracking, experiments and policy modules | No full corresponding capability set found in Ensemble tree | MISSING* | Decide which are part of Ensemble parity versus CE runtime/platform responsibilities. |

The MISSING entries are missing from the current Ensemble repository snapshot unless otherwise noted. Claude Code integration is an explicit ownership boundary, not a missing Ensemble parity item: Claude Code hooks/configuration, session lifecycle and Claude-specific workflows belong in the separate `ensemble-claude` repository. This does not exclude MCP server capability from Ensemble; the core MCP interface is required. General provider routing, arbitration, collaboration, execution, compression, alerts, messaging, cost tracking, experiments and policy still require individual client-neutral scope decisions rather than blanket inclusion or exclusion.

## Control Center findings

The accepted Control Center requirements are broader than the current implementation. The existing API mapping document records these concrete limitations:

1. Retrieval Explorer does not yet compare lexical, vector and hybrid candidates independently or show a correlated server-side trace.
2. Embeddings and Ranking/Reranking diagnostic workbenches are not yet present.
3. Pipeline Inspector is a preview, not a trace of a real ingestion or retrieval run.
4. Durable ingestion-job/checkpoint history is not available in the UI.
5. Operational metrics, migration/backup/restore diagnostics, and source/index recovery diagnostics remain incomplete.
6. Thompson task/capability-aware routing is not implemented.
7. Runtime configuration deliberately omits paths and database URLs, which is appropriate for secret minimization but limits operator diagnostics.

The Control Center requirements also explicitly prohibit pretending a skipped stage ran, showing placeholder metrics as live data, or bypassing REST contracts from browser code.

## Verification still required

The current review has been source-based. These scenarios are **NOT_RUN** until actually executed against both implementations:

- Shared black-box contract corpus for Memory, Graph, Knowledge, Index, Vector, Chunking, Tokenization, Embeddings, Reranking and Retrieval.
- Golden corpus comparison for chunk boundaries, tokenization, embedding metadata, ranking and retrieval.
- Restart tests for every durable store and stateful learning component.
- Failure injection for unavailable dependencies, partial writes, interrupted ingestion, and recovery.
- Backup/restore from non-empty stores, including generation/index consistency.
- Live POC-to-Ensemble and Ensemble-to-POC ledger transfer with byte/canonical-source invariance checks.
- Control Center browser-level tests for every workbench, including explicit unavailable/degraded/empty states.
- End-to-end ingestion and query pipeline traces, once actual correlated trace contracts exist.

## Recommended completion gates

1. **Freeze the parity boundary.** Ensemble is the client-neutral intelligence platform. Its required core includes RAG and first-class REST, MCP server, and GraphQL interfaces. Claude Code hooks/configuration, session lifecycle and Claude-specific workflows/adapters belong in `ensemble-claude`, not `ensemble`. Evaluate other POC runtime capabilities individually for client-neutral reuse.
2. **Build shared black-box fixtures.** Run the same input/output and failure scenarios against POC and Ensemble; compare observable behavior, not implementation technology.
3. **Close core data-path gaps.** Memory, Graph, Knowledge, Vector, Index, Chunking, Tokenization, Embeddings, Reranking, ingestion and retrieval.
4. **Close learning/optimization gaps.** Thompson, GA and Learning state, recovery, artifacts, and cross-capability side effects.
5. **Complete operator parity.** Control Center workbenches, real pipeline traces, diagnostics, metrics and recovery operations.
6. **Prove operations.** Migrations, backup/restore, restart/failure recovery, live ledger synchronization and full CI.
7. **Complete orchestration authority.** Implement Ensemble-owned planning, task/dependency state, scheduling, delegation, worker selection, coordination, cancellation, retry, checkpoint/recovery, governance and result synthesis. Treat SSH, local execution, GitHub and model providers as adapters beneath that authority.
8. **Deliver secure engineering automation.** Implement the authorized local workspace bridge, SSH-backed remote worker contract, Git operations, authenticated GitHub integration, and resumable/auditable engineering workflows. Validate least privilege, host verification, approval boundaries, secret handling, failure recovery and reviewable diffs.
9. **Close mandatory interfaces.** Complete and pass the RAG end-to-end acceptance suite, Ensemble MCP server contract suite, and GraphQL schema/resolver contract suite. REST, MCP and GraphQL must expose consistent client-neutral core behavior through shared domain services. These are required gates, not discretionary scope. Separately decide provider routing/arbitration, execution/collaboration, compression, alerts/messaging/cost/experiments/policy based on client-neutral platform value. Track Claude Code hooks/configuration and Claude-specific workflows separately in `ensemble-claude`; do not count them against Ensemble parity.

## Estimate

No honest overall percentage or remaining-hours estimate is possible until the parity boundary is frozen and the shared black-box suite has been run. The currently evidenced backend domains are mostly **PARTIAL**, not complete. If the whole POC product surface is required, the missing runtime/integration domains materially expand the work beyond the current Ensemble architecture. A useful estimate should follow the scope decision and first comparative test results, not the number of files or green unit tests.
