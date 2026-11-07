# Control Center API and UI Mapping

**Status:** Integration-branch implementation inventory, updated 2026-11-05.
**Purpose:** Describe the actual browser workbenches and their REST contracts. A UI panel is not proof of production readiness or complete capability parity.

## 1. Browser workbench coverage

| Workbench | Current behavior | REST operations | Known limitation |
|---|---|---|---|
| Platform status | Overall readiness, capability health/readiness, and a current snapshot of graph nodes/edges, vector collections, index sources, and recent persisted execution traces | `GET /ready`, capability `/health` and `/ready`, `GET /api/v1/graph/stats`, `GET /api/v1/index/manifest`, `GET /api/v1/vector/collections`, `GET /api/v1/execution-traces` | Snapshot only; no historical metrics or general durable-operation dashboard |
| Retrieval Explorer | Hybrid search, per-channel candidate counts/status, RRF contributions, final result provenance, intent/entity diagnostics, optional reranking | `POST /api/v1/search/hybrid` | Does not yet render each channel's complete ranked candidate list independently |
| Context Assembly | Compose Memory and Hybrid retrieval context with a bounded extractive budget | `POST /api/v1/context/assemble` | Character-based token estimate; no model-backed summarizer |
| Transcript ingestion | Submit structured session payload | `POST /api/v1/ingest/sessions` | Synchronous ingestion; no durable job/checkpoint UI |
| Index and Vector | Index manifest/source lookup/confirmed tombstone; vector collection inventory and embedding-backed similarity query with model/dimension checks | `GET /api/v1/index/manifest`, `GET /api/v1/vector/collections`, `POST /api/v1/embeddings/embed`, `POST /api/v1/vector/collections/{name}/query`, `GET /api/v1/index/sources/{source_id}`, `POST /api/v1/index/tombstones` | Exact vector scan, not ANN; generation rebuild/copy/reconcile/fence/activate/abort has REST coverage but no dedicated Control Center workflow |
| Memory and Knowledge | Memory names, Knowledge status, Memory search, Knowledge record lookup | `/api/v1/memory/*`, `/api/v1/knowledge/records/{source_id}` | No complete event/session lifecycle workbench or scoped filesystem scanner |
| URI ingestion | Preview followed by explicit ingestion | `POST /api/v1/ingest/urls/preview`, `POST /api/v1/ingest/urls` | Single-page fetch; no crawler or scheduled refresh |
| Graph | Aggregate node/edge counts by type and relationship, property-filtered node query, node lookup, bounded directional traversal | `GET /api/v1/graph/stats`, `POST /api/v1/graph/query`, `GET /api/v1/graph/nodes/{node_id}`, `POST /api/v1/graph/traverse` | Read-only inspection; no graph-wide visualization or activity history |
| Entities | Non-mutating extraction preview, explicit canonical resolution and typed linking | `POST /api/v1/entities/extract`, `/resolve`, `/link` | Deterministic exact resolution; no fuzzy/ontology workflow |
| Intent | Multi-intent classification and evidence inspection | `POST /api/v1/intent/classify` | Rule-based heuristic confidence, not model-calibrated |
| Chunking | Profile inventory and non-persistent chunk-boundary preview; also available as a diagnostic pipeline stage | `GET /api/v1/chunking/profiles`, `POST /api/v1/chunking/chunk` | Preview is not a persisted ingestion execution; actual instrumented ingestion stages are inspected through persisted traces |
| Tokenization | Analyzer profile inventory and token preview | `GET /api/v1/tokenization/profiles`, `POST /api/v1/tokenization/tokenize` | lexical-v1 only |
| Pipeline Inspector | Separate non-persistent diagnostic probes and persisted trace list/detail for actual instrumented ingestion/retrieval executions; stage status, duration, attributes, and trace/request identifiers | Capability preview endpoints; `GET /api/v1/execution-traces`, `GET /api/v1/execution-traces/{trace_id}` | Diagnostic probes are independent, not a replay; trace coverage is limited to instrumented operations and stores metadata rather than source content |
| GA | Launch bounded runs, inspect history/results/candidates, approve export candidate | `/api/v1/ga/runs`, `/runs/{run_id}`, `/runs/{run_id}/promote` | Synthetic local evaluators; no live settings are applied |
| Thompson | Inspect per-model successes/failures, calls, cost/token totals, derived alpha/beta parameters and posterior mean; request a cost-bounded model selection; inspect persisted selection history; reset a model after confirmation | `GET /api/v1/thompson/state`, `GET /api/v1/thompson/selections`, `POST /api/v1/thompson/select`, `POST /api/v1/thompson/reset` | Selection is sampled; task/capability filtering is not implemented, and the UI must not imply that the task field filters eligibility |
| Learning | Report, recent outcomes, portable artifacts, idempotent outcome/artifact recording, retry-safe Memory and Thompson acknowledgement, confirmed reset | `/api/v1/learning/*` | No automated Knowledge promotion; synthetic GA artifacts remain ineligible |
| Runtime configuration | Display listener and persistence status | `GET /api/v1/system/config` | Deliberately omits filesystem paths and database URLs |

## 2. REST capability inventory

The Control Center is a REST consumer. It does not access SQLite directly and has no UI-only persistence path.

- **System:** health, readiness, non-sensitive runtime configuration.
- **Context:** health, readiness, bounded extractive context assembly.
- **Embeddings:** health, model metadata, controlled embedding probe.
- **Chunking / Tokenization:** health, readiness, profile inventory, preview operations.
- **Entities / Intent:** health, readiness, extraction, explicit resolution/linking, classification.
- **Ranking / Reranking:** health, readiness, fusion and rerank operations.
- **Search:** default, lexical and hybrid retrieval; Knowledge-scoped retrieval.
- **Vector:** collection inspection, upsert, lookup, metadata update, query and pruning.
- **Index:** revision decisions, tombstones, source lookup and bounded manifest.
- **Knowledge:** health, readiness, record publication/lookup and tombstones.
- **Memory:** named memory CRUD, entries, append-once, recall, lexical/semantic/hybrid search, context assembly, source import/reconciliation/status, event capture.
- **Graph:** node/edge resources, lookup, query and bounded traversal.
- **Ingestion:** documents, files, URLs, transcripts and structured sessions.
- **GA / Thompson / Learning:** bounded optimization runs, model outcome statistics and learning outcome/artifact workflows.

## 3. Diagnostic semantics

The Pipeline Inspector has two distinct read-only surfaces. Diagnostic preview invokes current capability operations independently and reports those probe results; it is not a replay and does not mutate persistence. Persisted trace inspection reads actual server-side execution traces for instrumented ingestion and retrieval operations, including recorded stages and timings. A preview result must never be represented as a persisted execution trace.

The Context Assembly endpoint is a real server-side operation. It combines current retrieval candidates and optional Memory recall, preserves source metadata, applies deterministic extractive selection, and reports an approximate character-based token budget. It does not use a model tokenizer or model-backed summarizer.

A future correlated trace must be emitted by the actual ingestion/retrieval execution, with a stable run identifier, bounded stage events, explicit failure/degradation states, and a defined redaction/retention policy. Re-running a query is not a historical trace.

## 4. Remaining Control Center work

1. Add independent lexical/vector candidate comparison alongside the existing hybrid fusion/reranking explanation and provenance.
2. Add dedicated Embeddings and Ranking/Reranking workbenches for provider identity, readiness, and before/after candidate inspection.
3. Add bounded Index generation rebuild/copy/reconcile/fence/activate/abort operations to Control Center; the workbench operates one page per capability and requires explicit confirmation for write-fence acquisition, activation, and abort.
4. Add bounded operational metrics from backend measurements, with units and time windows, plus general durable-operation history where backend contracts support it.
5. Add safe migration, backup/restore, and source/index recovery diagnostics.
6. Preserve explicit confirmation for destructive maintenance operations.

## 5. Implementation caveats

- Endpoint presence does not establish production readiness, full POC parity, or successful operation under restart/failure.
- Exact vector search scans SQLite records; it is not ANN.
- Intent and entity extraction are deterministic/rule-based; confidence is heuristic.
- Transcript JSONL parsing accepts caller-supplied byte slices and returns a checkpoint; durable checkpoint jobs are not implemented.
- URI ingestion is a bounded single-page fetch, not a crawler.
- Authentication, authorization, partition enforcement, rate limits, and production observability remain open.
- Keep every workbench backed by the canonical REST contract. Do not move business logic or persistence into browser code.
