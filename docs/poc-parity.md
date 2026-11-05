# POC Capability and Reference Inventory

Reference: FlossWare/poc-claude-ensemble
Target: FlossWare/ensemble
Target deployment: one application, one REST listener, independently addressable capability endpoint groups, plus a browser-based Control Center.
Persistence boundary: SQLite is Ensemble's own persistence implementation. PostgreSQL and alternative/shared persistence implementations belong to FlossWare proper and are not Ensemble deliverables.

## Reference policy

This document records the POC's capability surface and useful behavioral evidence. It is not Ensemble's acceptance specification, and POC route names, implementation details, or test counts are not requirements. Ensemble's current contracts and functionality-first completion plan are authoritative. The POC helps identify omissions and edge cases, but every behavior must be assessed against Ensemble's architecture and intended use.

Disposition meanings:
- Reuse: source implementation is sound and can be incorporated with minimal adaptation.
- Adapt: preserve behavior/algorithm, redesign implementation for Ensemble contracts and the one-server architecture.
- Replace: preserve required behavior, replace implementation because its design or dependencies do not fit.
- Reference: use as behavioral evidence only; do not carry code forward.
- Not assessed: full source, tests, edge cases, and dependencies have not yet been inspected.

The table below is a reference inventory, not a parity certification. Intentional differences should be documented when they affect caller-visible behavior, data integrity, or operational expectations.

## Complete POC service inventory

The POC README declares 13 services. All 13 are recorded here as reference material. Their inclusion does not make every POC behavior an Ensemble requirement.

| POC capability | POC location / contract | Required Ensemble capability | Current Ensemble state | Initial disposition |
|---|---|---|---|---|
| Embedding | services/embeddings, port 8770, embeddings/model/health | Document/query embeddings, model identity and revision, dimensions, runtime provenance, limits, provider selection, health | Pinned local Nomic implementation mirrors the POC embed_lib algorithm and supports document/query prefixes, 512-token truncation, masked mean pooling, L2 normalization, pinned revision, ONNX Runtime CPU FP32, OpenVINO CPU FP32 and explicit OpenVINO GPU FP16 fail-closed selection; OpenAI-compatible provider is optional. Fake-runtime tests pass; a real-model smoke test skips when weights are absent; hardware runtime matrix remains to be exercised | Adapt |
| Vector | services/embeddings, port 8771, collection query/upsert/existing/delete-superseded | Vector collection operations, deterministic IDs, upsert, search, deletion/revision handling, health | SQLite-backed named collections and all POC collection routes implemented; exact dot-product search, model/dimension compatibility, and transcript_text upsert/query integration in Hybrid; review branch CI tests pass; full POC regression parity remains outstanding | Replace |
| Lexical | services/embeddings, port 8772, search/documents | Incremental idempotent lexical indexing, analyzed fields, BM25/FTS retrieval, source metadata | SQLite FTS5 BM25 indexes versioned document chunks using shared lexical-v1 tokenization, Porter stemming, compound identifiers, OR matching, and transactional revision replacement; `/lexical/documents` accepts the POC session-plus-messages contract and writes idempotent transcript messages into Lexical, Memory events, Vector when configured, and Graph; search results preserve session_id, project, role, kind, is_subagent and text SHA-256 deduplication | Adapt |
| Hybrid | services/embeddings, port 8773, search/knowledge-search/prompts | Orchestrated multi-channel retrieval, knowledge/transcript retrieval, partial dependency degradation, ingestion/event handling | REST groups expose hybrid search, knowledge-only vector search, idempotent prompt capture, successful-search event logging, and bounded session ingestion; lexical/vector retrieval has embedding-degradation fallback, optional cross-encoder reranking, rule-based intent enrichment, exact-path related-session graph lookup, and durable message/event/graph writes, with transcript embeddings stored and queried through the Vector capability. Search-ledger write failures do not fail retrieval. Knowledge and transcript/document candidates now share the durable document/chunk retrieval path; exact entity Graph expansion augments candidate fusion. An optional HTTP ranking-provider adapter is now implemented behind the shared reranking decorator; calibrated channel weighting, request deadlines/cancellation, and full trace provenance remain outstanding | Adapt |
| Reranking | services/embeddings, port 8774, rerank | Cross-encoder candidate reranking, model metadata, bounded inputs, aligned scores, health/readiness | Optional pinned local CrossEncoder provider and REST route; opt-in Hybrid integration reranks up to 30 candidates, validates score alignment/finite values, preserves stable ties, and degrades without losing fused results. Local model runtime verification remains pending | Adapt |
| Chunking | services/chunking, port 8775, profiles/chunk | Versioned deterministic profiles: window, Markdown, message; stable chunk IDs and source offsets/provenance | All three profiles are used by document ingestion; selected profile, exact source offsets and Markdown heading provenance persist in SQLite and are returned in search hits; GitHub Actions regression coverage added | Adapt |
| Tokenization | services/tokenization, port 8776, profiles/tokenize | Versioned deterministic analyzers, compound identifier handling, exact offsets, consistent index/query analysis | lexical-v1 pure logic and REST routes added; review branch CI tests pass; full POC regression parity remains outstanding | Adapt |
| Index | services/index, port 8777, revisions/tombstones/sources/manifest | Source manifest, monotonic revision sequencing, profile provenance, duplicate/conflict/superseded decisions, tombstones, bounded manifest | SQLite manifest, revision decisions, permanent tombstones, source lookup and bounded manifest routes implemented; Index-gated Knowledge publication now writes the shared document/chunk index, vector collection when embeddings are available, and exact entity/source Graph relationships; branch CI tests pass; full POC regression parity remains outstanding | Adapt |
| Extraction | services/extraction, port 8778, extract | Exact identifier extraction and uncertain entity candidates with evidence, offsets and confidence | POC rule set and REST routes added; parity branch CI tests pass; full POC regression parity remains outstanding | Adapt |
| Intent | services/intent, port 8779, classify | Explainable intent labels, confidence, matched evidence and per-label scores | POC rule set and REST routes added; parity branch CI tests pass; full POC regression parity remains outstanding | Adapt |
| Ranking | services/ranking, port 8780, rank | Deterministic reciprocal-rank fusion with per-list score contributions and stable tie-breaking | POC-compatible fusion logic and REST routes added; parity branch CI tests pass; full POC regression parity remains outstanding | Adapt |
| Knowledge | services/knowledge, port 8781, records/tombstone | Sourced records, origin/locator provenance, revision checks through Index, idempotency and deletion | SQLite sourced-record storage, SHA-256 content digest, Index-gated revision decisions, provenance, tombstones and REST routes implemented; branch CI tests pass; full POC regression parity remains outstanding | Adapt |
| Memory | services/memory, port 8767, memory operations and session-events ledger | Durable episodic memory, session event ledger, concurrent access, path/scope validation, recall and memory lifecycle | SQLite implementation: read/write, append/append-once, entries, list, lexical and deterministic local semantic/hybrid search, execution-context recall, Markdown section chunking, SHA-256-verified Markdown source import, stale-source reconciliation and knowledge status are exposed through the single REST listener. Direct scoped filesystem scanning and symlink-safe server-side traversal are intentionally replaced by caller-supplied content plus declared-scope validation; the SessionEnd ledger envelope, durable append-once behavior, and caller-supplied timestamp identity are preserved in SQLite. Filesystem scanning and hook delivery remain consumer-integration responsibilities, not Ensemble server responsibilities; embedding-backed Memory search remains open | Replace |
| Graph | services/graph-service, port 8766, add-node/add-edge/node/query/traverse | Stable node/edge identities, typed properties, durable mutations, node/edge query and bounded directional traversal | SQLite graph adapter and REST endpoints implemented; API and restart-persistence tests pass in CI | Replace |
| URI scraping | POC ingestion workflows and source discovery | Safe public-URI fetch, readable extraction, preview, explicit ingest, bounded link discovery and observable job lifecycle | Initial single-page preview and explicit ingest endpoints plus browser UI implemented; bounded multi-page crawl, robots policy, scheduling, and durable job history pending | Adapt |

## Knowledge implementation

Ensemble stores the canonical sourced record, content SHA-256, origin/locator provenance and profile snapshot in SQLite. Every publication consults the Index capability first; only accepted or duplicate revisions are stored. Tombstones are recorded in Index before the knowledge record is marked deleted, and future revisions remain blocked. This preserves the POC's cross-capability decision flow without making the POC service or database a runtime dependency. Alembic migration `0004_knowledge_records` owns the record schema.

## Index implementation

Ensemble implements the POC's source-manifest decision model in SQLite: accepted, duplicate, conflict, superseded, and tombstoned revision outcomes; profile-version snapshots; permanent tombstones; source lookup; and bounded manifest listing. The Index capability gates supported document, file, PDF, URL, transcript, lexical, and Knowledge publication writes. Accepted content is stored in the shared document/chunk index; tombstones remove active document chunks, Knowledge records, and vectors across collections. Alembic migration `0003_source_index` owns its schema.

## Vector storage implementation

Ensemble replaces ChromaDB with its own SQLite-backed vector collections. Collection model and dimensions are immutable once created; records are upserted by stable ID; exact dot-product search is deterministic with ID tie-breaking; metadata updates and source-revision cleanup are supported. This is functionally compatible with the POC operations but does not claim ANN scalability. Vector tables are introduced by Alembic migration `0002_vector_collections`.

## Embedding runtime behavior to preserve or explicitly supersede

The POC embedding contract uses Nomic nomic-embed-text-v1.5, 768 dimensions, search_document: and search_query: prefixes, masked mean pooling, and L2 normalization. Runtime choices are ONNX Runtime CPU FP32 (default), OpenVINO CPU FP32, and OpenVINO GPU FP16. Explicit GPU selection fails closed rather than silently falling back to CPU. Model identity, pinned revision, actual device, graph precision, and runtime provenance are reported.

Ensemble may support additional providers and models, but must preserve equivalent model identity/provenance, query-vs-document semantics, dimensions, and explicit failure behavior. A provider must never silently claim GPU execution after falling back to CPU.

## Retrieval behavior to preserve

- Vector and lexical retrieval are separate candidate channels.
- Hybrid search fuses channels using reciprocal-rank fusion, with POC default k=60.
- Candidate provenance distinguishes transcript and knowledge sources and retains source locator, title, chunk identity/position, and content digest where applicable.
- A failed channel is reported as degraded while successful channels can still contribute. Return service-unavailable only when no candidate source succeeds.
- Optional cross-encoder reranking operates on a bounded candidate set after authorization filtering.
- Knowledge-only retrieval remains independently callable.
- Results and fusion contributions must be explainable and deterministic where the POC guarantees determinism.

## Control Center parity

The POC capability set must be inspectable and operable from a real browser UI, not only Swagger/OpenAPI.

| Area | Required functionality |
|---|---|
| Overview | Application health/readiness, capability status, provider status, active configuration, throughput/latency/error indicators |
| Capability Explorer | Enumerate all 13 capabilities, contracts, versions, dependencies, health and readiness |
| Retrieval Explorer | Submit query; inspect lexical/vector candidates, fused ranks/contributions, reranking scores, provenance and degraded channels |
| Documents and Indexes | Inspect sources, revisions, tombstones, chunks, profiles, indexing state and provenance |
| Memory and Knowledge | Inspect scoped records/events, provenance, revisions, recall results and lifecycle operations |
| Models and Providers | Inspect embedding/reranking model IDs, revisions, dimensions, runtime/device/precision, configured providers and failures |
| Configuration | View and safely change supported chunking, tokenization, retrieval, ranking, provider and timeout settings |
| GA Tuning | Configure and launch bounded runs, inspect fitness history and candidate parameters, compare candidates, and explicitly approve export without automatic live application |
| Thompson | Inspect model statistics, request cost-constrained model selection, and explicitly reset per-model learning state |
| Operations | Trigger permitted ingestion/reindex/maintenance operations, inspect jobs/results, confirm destructive actions |
| Diagnostics | Structured logs, correlation IDs, dependency failures, latency breakdowns, persistence health and exportable diagnostics |

The UI is served by the same Ensemble application and uses the same REST contracts. It must not introduce a second backend or duplicate domain logic. Administrative operations are localhost-only initially; remote administration requires a designed authentication/authorization boundary. Do not expose arbitrary shell execution.

## Cross-cutting parity requirements

- One FastAPI application and one REST listener; logical capability boundaries remain independently addressable.
- Stable, versioned, language-neutral REST contracts with consistent Problem Details errors.
- Explicit health and readiness per capability, including dependency state.
- Bounded request sizes, candidate counts, timeouts and concurrency.
- Deterministic/idempotent ingestion and retry semantics where applicable.
- Source, revision, chunk, model and analyzer provenance.
- Observable partial failure and degraded retrieval behavior.
- No implicit data sharing across users/partitions; access filtering precedes fusion and reranking.
- No POC runtime dependency or direct sharing of POC database files/directories.
- SQLite only in this repository. Shared infrastructure and alternate persistence implementations are owned by FlossWare proper.
- No mandatory generation model or MCP dependency.

## POC HTTP route catalog

The source handlers expose the following route surface. These are the behavioral reference routes; Ensemble may normalize their naming under /api/v1, but must account for each operation in its versioned contracts.

| Capability | POC GET routes | POC POST routes |
|---|---|---|
| Embedding | /health, /model | /embeddings |
| Vector | /health, /collections | /collections/{name}/existing, /upsert, /metadata, /delete-superseded, /query |
| Lexical | /health | /search, /documents |
| Hybrid | /health | /search, /knowledge/search, /prompts, /sessions/ingest |
| Reranking | /health | /rerank |
| Chunking | /health, /ready, /profiles | /chunk |
| Tokenization | /health, /ready, /profiles | /tokenize |
| Index | /health, /ready, /sources/{source_id}, /manifest | /revisions, /tombstones |
| Extraction | /health, /ready | /extract |
| Intent | /health, /ready | /classify |
| Ranking | /health, /ready | /rank |
| Knowledge | /health, /ready, /records/{source_id} | /records, /records/{source_id}/tombstone |
| Memory | /health, /ready | /memory/read, /memory/write, /memory/append, /memory/append-once, /memory/entries, /memory/retrieve, /memory/list, /memory/search, /memory/search-semantic, /memory/search-hybrid, /memory/chunk, /memory/ingest, /memory/reconcile, /memory/knowledge/status |

The vector service also exposes /collections/{name}/metadata as a POST operation for metadata updates. The memory API includes both search and search-semantic aliases; preserve compatibility intentionally rather than accidentally dropping an alias. The POC route table is derived from the source handlers, not inferred from README prose alone.

## Source and regression-test inventory

The POC source and matching service test files have now been located and inspected at the contract/test-name level. The listed test files contain 271 top-level test cases in total (counted from test method declarations). This is an inventory count, not evidence that the POC suite was executed during this task.

| Capability | POC source | POC tests | Test cases identified |
|---|---|---|---:|
| Embedding | services/embeddings/embedding_service.py; services/embeddings/embed_lib.py | services/embeddings/test_embedding_service.py | 24 |
| Vector | services/embeddings/vector_service.py; services/embeddings/vector_store.py | services/embeddings/test_vector_service.py | 26 |
| Lexical | services/embeddings/lexical_service.py | services/embeddings/test_lexical_service.py | 45 |
| Hybrid | services/embeddings/hybrid_service.py | services/embeddings/test_hybrid_service.py | 49 |
| Reranking | services/embeddings/reranking_service.py | services/embeddings/test_reranking_service.py | 9 |
| Chunking | services/chunking/chunking_service.py | services/chunking/test_chunking_service.py | 14 |
| Tokenization | services/tokenization/tokenization_service.py | services/tokenization/test_tokenization_service.py | 13 |
| Index | services/index/index_service.py | services/index/test_index_service.py | 15 |
| Extraction | services/extraction/extraction_service.py | services/extraction/test_extraction_service.py | 7 |
| Intent | services/intent/intent_service.py | services/intent/test_intent_service.py | 6 |
| Ranking | services/ranking/ranking_service.py | services/ranking/test_ranking_service.py | 8 |
| Knowledge | services/knowledge/knowledge_service.py | services/knowledge/test_knowledge_service.py | 7 |
| Memory | services/memory/memory_service.py | services/memory/test_memory_service.py | 48 |

Additional shared implementation dependencies identified:
- services/execution/context.py: versioned execution-context/result contracts and bounded serialization.
- services/shared/runtime_config.py: shared runtime, memory, log, learning and socket path configuration.
- services/embeddings/turn_outcomes.py: transcript outcome ledger and Thompson REST integration helper. The POC README explicitly says transcript Thompson outcome recording is not yet built.
- services/embeddings/migrate_lexical_tokenization.py: guarded, resumable FTS5 migration to tokenization-service analysis.
- services/embeddings/build_knowledge_chunks.py: resumable REST-driven knowledge ingest, chunk, embed and vector-write pipeline.
- services/embeddings/vector_store.py: persistent ChromaDB collection configuration and model/dimension compatibility checks.

Important behavior surfaced by the tests and source:
- Embedding: model/runtime provenance; CPU/GPU fail-closed selection; shape, normalization and semantic similarity; strict request validation; serialized model access.
- Vector: exact cosine/dot-product ordering; persistent reload; deterministic upsert replacement; model/collection compatibility; dimensions and finite-value validation; bounded batches; loopback-only binding.
- Lexical: FTS5/BM25, Porter stemming, analyzed term persistence, incremental idempotent ingest, deduplication, source metadata, and fail-closed dependency on tokenization.
- Hybrid: partial-success retrieval and degraded dependency reporting; RRF; transcript JSONL parsing with offset-based resume; path and symlink containment; prompt/event deduplication; ingestion progress; knowledge provenance; bounded caching and no caching of partial/failing results.
- Reranking: aligned scores, stable results, real cross-encoder relevance, bounded/validated requests, serialized model access and readiness failure when the model is absent.
- Chunking: stable IDs, profile-version isolation, exact Unicode offsets, overlap coverage, Markdown heading provenance and message clipping.
- Tokenization: Unicode case folding, minimum token length, compound identifier whole/component terms, UUID handling and exact offsets.
- Index: monotonic revision high-water mark, duplicate/conflict/superseded decisions, profile-version conflict detection, tombstone permanence and persistence across restart.
- Extraction: exact identifier spelling and offsets, confidence/exactness distinction, uncertain name candidates and non-overlapping spans.
- Intent: explicit labels, matched-rule evidence, zero-confidence no-evidence result and deterministic bounded confidence.
- Ranking: RRF contributions, order independence, duplicate suppression, stable key tie-breaking and input/output bounds.
- Knowledge: Index-gated revision publication, provenance, current-record lookup, tombstones, and explicit failure when Index is unavailable.
- Memory: this is substantially more than key/value memory. The service includes append-once durable event records, stable event IDs and payload digests, fsync/retry behavior, semantic search/index warming, incremental file change detection, exact-identifier prioritization, execution-aware prior-context retrieval, schema-versioned execution context, readiness independent of liveness, bounded warmup/retry behavior, private runtime/socket permissions, and path/name validation. Preserve these behaviors or document deliberate replacements.

The POC README also identifies worker/arbiter quality monitoring as not yet built. It is not a parity obligation for an unimplemented POC service. Keep it as a separate future consideration rather than silently inventing scope.

## Work status

The catalog and primary service/test files have been inventoried. This document remains a discovery aid. Current implementation state, integrated workflows, and known limitations are maintained in implementation-status.md and capability-completion-plan.md.

Before implementing each capability, complete a source audit recording:
1. POC source files and tests.
2. Public endpoints and request/response schemas.
3. Algorithms, model IDs/revisions, profiles and defaults.
4. Persistence and idempotency behavior.
5. Validation, error, health/readiness and degraded-mode behavior.
6. External dependencies and runtime assumptions.
7. Ensemble target module/contract and reuse/adapt/replace/reference decision.
8. Contract and regression tests proving parity.

Do not mark parity complete based solely on matching endpoint names or a green unit-test suite.


## URI scraping and Control Center

The browser Control Center is served by the same FastAPI application at `/control`; its JavaScript uses the same REST listener. The first ingestion workflow accepts one public HTTP(S) URI, rejects local/private/reserved destinations, bounds redirects, response size and duration, extracts readable HTML or plain text, and presents a preview. Preview is read-only. The user must explicitly select Ingest before the document pipeline is called. Discovered links are shown as future crawl candidates and are not fetched automatically.

This is the first slice, not full crawler parity. Remaining work includes robots.txt policy, stronger DNS-to-connection binding against rebinding, bounded same-origin crawl selection, deduplication, durable ingestion jobs/progress/retry, and source refresh scheduling. Do not expose arbitrary network targets or shell execution through the Control Center.


## Additional POC modules outside the 13-service README catalog

The repository also contains separate Graph, Learning, Thompson, GA Tuning, scrape-ledger, arbitration, collaboration, workflow, cost-tracking, policy, decision-provenance, operational-metrics, experiments, and provider modules. These are not all listed as the 13 core REST services in the POC README. They require a separate disposition inventory before parity can be declared; do not silently treat their existence as proof that each is a required Ensemble capability. Graph and GA Tuning are explicitly included in Ensemble's parity scope.


## Graph implementation

Ensemble adapts the POC Graph service contract into the same REST application. It provides deterministic content-derived node and edge IDs, idempotent add operations, typed node and relationship queries, node lookup, and bounded directional traversal. SQLite tables persist graph mutations and the graph can be reconstructed after restart. This replaces the POC's NetworkX plus atomic JSON-file persistence while retaining its public operation semantics. The graph does not introduce a separate listener or external graph database.


## Genetic Algorithm (GA) tuning

The POC contains a substantial GA subsystem at `core/ga_tuning`, separate from the 13 core REST services. It is in Ensemble's parity scope. It must not be mistaken for the generic ranking or Thompson capabilities.

The POC GA evolves parameter sets for five evaluators:
- Compression: token savings constrained by semantic preservation.
- Thompson router: cost savings while maintaining quality.
- Caching: cache hit rate and token savings with false-negative constraints.
- Capability matrix: model-selection/routing accuracy.
- Dashboard learning: learning speed and stability under concept drift.

The reference engine uses population-based evolution, tournament selection, crossover, Gaussian mutation, elite preservation, bounded parameter schemas, reproducible runs, fitness history, and best-parameter artifacts. The POC also includes a GA tuning dashboard, a scheduled runner/workflow, parameter extraction/application support, evaluator-specific tests, and recovery/scheduler contract tests.

Ensemble's initial GA implementation is now available through the existing REST application:
- `GET /api/v1/ga/health`, `/ready`, and `/optimizers`.
- `POST /api/v1/ga/runs` starts a bounded background run; `GET /api/v1/ga/runs` and `/runs/{id}` expose status, configuration, results, fitness history and best candidates.
- `POST /api/v1/ga/runs/{id}/promote` requires explicit approval and records a candidate for export only. It never applies live settings.
- SQLite migration `0007_ga_runs` persists run configuration, status, results and promotion records.
- The initial engine implements seeded reproducible population evolution, tournament selection, crossover, Gaussian mutation, elite preservation, and five synthetic local evaluator families.

This is an initial implementation, not yet a parity certification. The five evaluators are adapted synthetic-local implementations and their scoring behavior still needs comparison against the POC's evaluator tests and edge cases. Scheduled execution/recovery, portable learning artifacts, and Control Center configuration/progress/candidate comparison remain outstanding. The GA Thompson evaluator is separate from the standalone Thompson service, which remains a separate parity obligation.

The POC evaluators use local evaluation and synthetic or configured corpora; do not imply their fitness scores establish production quality. Any evaluator that depends on unavailable RH-only data or consumers must report that limitation rather than fabricate a successful evaluation. GA is a first-class Ensemble capability but remains SQLite-backed and hosted by the single Ensemble REST application; no second server is introduced.


## Initial Memory implementation

Alembic migration `0006_memory` adds SQLite-backed named memory documents and append-only event records. The implementation exposes Memory through the existing FastAPI application; it does not start another process or listener. Append-once uses a unique (memory name, event ID) key and a canonical JSON SHA-256 digest: retries with identical content return duplicate, while reuse with different content conflicts. Execution-context recall prioritizes parent, ancestor, same-request and related-lineage records and excludes descendants. The initial lexical search and deterministic character chunking are deliberately limited; they do not claim semantic-search parity.

The initial REST operations are `/api/v1/memory/health`, `/ready`, `/read`, `/write`, `/append`, `/append-once`, `/entries`, `/retrieve`, `/list`, `/search`, `/search-semantic`, `/search-hybrid`, `/chunk`, `/ingest`, `/reconcile`, and `/knowledge/status`. The semantic route uses deterministic local term-vector cosine similarity; it is not an external embedding service or ANN index. Source import requires caller-supplied content plus a matching SHA-256 and an absolute path within a declared scope; Ensemble does not open arbitrary local paths. Direct filesystem scanning, symlink-safe traversal and full event-ledger compatibility remain open parity work. Tests cover REST behavior, append-once idempotency/conflict handling, context recall, validation, and chunking; the CI run for the initial Memory API/test commit passed.


## Initial standalone Thompson implementation

The POC Thompson router is a distinct capability from the GA Thompson evaluator. Ensemble exposes it through the existing listener at `/api/v1/thompson`: health/readiness, ping, model selection, outcome recording, state inspection and model reset. The POC action-style POST contract is retained as a compatibility endpoint while explicit REST routes are also available. Selection samples Beta(successes + 1, failures + 1) for each model, filters by observed average cost while allowing untested models, and falls back to the cheapest model if all tested models exceed the cost limit. Empty state falls back to `haiku`.

SQLite migration `0008_thompson` persists per-model successes, failures, cost, tokens, calls and last-update time. Outcome increments are atomic. The browser Control Center can inspect statistics, request a model selection, and reset a model after confirmation. The POC's `task_type` and `required_capability` inputs are retained but not used by its current selection algorithm; no capability matrix filtering is claimed. Learning now records Thompson outcomes through an idempotent event API and persists Thompson state snapshots in Memory. A separate network client/circuit-breaker library remains outside this co-located implementation. The Control Center requirement for selection history is implemented as an Ensemble extension beyond the POC algorithm: each selection is persisted with its selected model, task type, capability threshold, cost limit, request ID and timestamp. SQLite history is bounded to the latest 5,000 entries, and the REST inspector returns at most 100 entries per request. Resetting model statistics does not erase this audit history.


## Initial Learning implementation

Learning is hosted by the existing Ensemble application and shares its Memory and Thompson stores; it does not start a daemon or second listener. The API exposes health/readiness/ping, outcome processing, outcome lookup, recent outcomes, reports, portable artifact recording/listing and explicit reset. The POC `process_outcome` and `record_artifact` action names are retained at `POST /api/v1/learning` while dedicated REST routes are also available.

Migration `0010_learning` persists task outcomes and portable artifacts. Each task ID is bound to a canonical payload SHA-256; reuse with a different payload conflicts. Outcome processing publishes an append-once Memory event, updates Thompson through an idempotent event key, snapshots Thompson state into Memory, and advances the Learning checkpoint only after all steps succeed. This makes retries safe across partial failures without relying on a cross-service database transaction. Thompson event keys and model-stat increments are committed atomically in migration `0011_thompson_events`.

Portable artifacts preserve the POC's `learning-artifact` schema/version, artifact type, source, payload, provenance and creation timestamp. `artifact_type:run_id` is the idempotency key. Reset requires explicit confirmation and clears Learning outcomes while preserving Thompson model state and portable artifacts. The Control Center can inspect reports, recent outcomes and artifacts and perform the confirmed reset.

Remaining parity work includes deeper comparison against the POC's learning-service test/edge cases and any remote Thompson client/circuit-breaker behavior. The source audit found that the Learning Service's `AutonomousLearningSystem` creates an `autonomous_priors` directory and clears its JSON files during reset, but its `process_outcome` path does not update those priors. Actual per-model/per-task `PriorUpdater` behavior lives in the separate `services/learning/autonomous_learning.py` coordinator; it requires explicit external ground-truth signals, persists one JSON file per model/task pair, and is not wired into the Learning Service request path. No dedicated PriorUpdater regression suite was found in the POC inventory. Treat that coordinator as a separate future autonomous-learning capability, not a missing Learning REST operation; do not port it into Ensemble until its acceptance contract is explicitly brought into scope. The current Ensemble Learning implementation is local to the single process and uses SQLite plus the shared Memory event ledger.
