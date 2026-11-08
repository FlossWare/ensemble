# Control Center API and UI Mapping

**Status:** Review-branch implementation inventory for `review/core-services-2026-11-02`.
**Purpose:** Describe the actual browser workbenches and their REST contracts. A UI panel is not proof of production readiness or complete capability parity.

## 1. Browser workbench coverage

| Workbench | Current behavior | REST operations | Known limitation |
|---|---|---|---|
| Platform status | Overall readiness and capability health/readiness | `GET /ready`, capability `/health` and `/ready` | No historical metrics or durable operation dashboard |
| Retrieval Explorer | Hybrid search, result provenance, intent/entity diagnostics, optional reranking | `POST /api/v1/search/hybrid` | Does not yet compare each retrieval channel independently or show a correlated server-side trace |
| Context Assembly | Compose Memory and Hybrid retrieval context with a bounded extractive budget | `POST /api/v1/context/assemble` | Character-based token estimate; no model-backed summarizer |
| Transcript ingestion | Submit structured session payload | `POST /api/v1/ingest/sessions` | Synchronous ingestion; no durable job/checkpoint UI |
| Index and Vector | Manifest, collection list, source lookup, confirmed tombstone | `GET /api/v1/index/manifest`, `GET /api/v1/vector/collections`, `GET /api/v1/index/sources/{source_id}`, `POST /api/v1/index/tombstones` | No index-generation rebuild or cleanup-retry workbench |
| Memory and Knowledge | Memory names, Knowledge status, Memory search, Knowledge record lookup | `/api/v1/memory/*`, `/api/v1/knowledge/records/{source_id}` | No complete event/session lifecycle workbench or scoped filesystem scanner |
| URI ingestion | Preview followed by explicit ingestion | `POST /api/v1/ingest/urls/preview`, `POST /api/v1/ingest/urls` | Single-page fetch; no crawler or scheduled refresh |
| Graph | Property-filtered node query, node lookup, bounded traversal | `POST /api/v1/graph/query`, `GET /api/v1/graph/nodes/{node_id}`, `POST /api/v1/graph/traverse` | No graph-wide visualization or activity history |
| Entities | Non-mutating extraction preview, explicit canonical resolution and typed linking | `POST /api/v1/entities/extract`, `/resolve`, `/link` | Deterministic exact resolution; no fuzzy/ontology workflow |
| Intent | Multi-intent classification and evidence inspection | `POST /api/v1/intent/classify` | Rule-based heuristic confidence, not model-calibrated |
| Chunking | Profile inventory and non-persistent chunk preview | `GET /api/v1/chunking/profiles`, `POST /api/v1/chunking/chunk` | No persisted ingestion trace |
| Tokenization | Analyzer profile inventory and token preview | `GET /api/v1/tokenization/profiles`, `POST /api/v1/tokenization/tokenize` | lexical-v1 only |
| Pipeline Inspector | Sequential read-only probes for tokenization, entities, intent and chunking | Existing capability preview endpoints | Interactive diagnostic preview, not a correlated trace of a real ingestion |
| GA | Launch bounded runs, inspect history/results/candidates, approve export candidate | `/api/v1/ga/runs`, `/runs/{run_id}`, `/runs/{run_id}/promote` | Synthetic local evaluators; no live settings are applied |
| Thompson | Inspect state, select model, record outcomes, reset after confirmation | `/api/v1/thompson/*` | task/capability filtering is not implemented |
| Learning | Report, recent outcomes, artifacts, confirmed reset | `/api/v1/learning/*` | Initial workflow; deeper parity and recovery remain open |
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

The Pipeline Inspector is intentionally an interactive preview. It invokes current capability operations independently and reports their results. It does not mutate persistence and does not claim to inspect a previous ingestion or retrieval execution.

The Context Assembly endpoint is a real server-side operation. It combines current retrieval candidates and optional Memory recall, preserves source metadata, applies deterministic extractive selection, and reports an approximate character-based token budget. It does not use a model tokenizer or model-backed summarizer.

A future correlated trace must be emitted by the actual ingestion/retrieval execution, with a stable run identifier, bounded stage events, explicit failure/degradation states, and a defined redaction/retention policy. Re-running a query is not a historical trace.

## 4. Remaining Control Center work

1. Compare lexical, vector and hybrid candidates independently, including fusion contributions and provenance.
2. Add Embeddings and Ranking/Reranking diagnostic workbenches.
3. Add durable ingestion-job/checkpoint history only after those server-side job semantics exist.
4. Add bounded operational metrics from backend measurements, with units and time windows.
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


## Federation workbench status

The LAN discovery slice is implemented. The browser polls `GET /federation/overview`, which inspects each discovered peer through its `/health` and `/ready` endpoints and renders remote reachability/readiness plus capabilities. `GET /federation/status` provides local discovery state and the peer registry; `GET /federation/peers` provides the peer-list resource. The backend owns multicast discovery, peer expiry, and remote inspection; browser code does not scan the network or maintain an independent registry.

Still planned: peer capability detail negotiation, resource/health telemetry, reliable messaging, service/task delegation, federated RAG, and derived-artifact transfer visibility. The current peer list is in-memory and repopulates after restart. See [Control Center Requirements](control-center-requirements.md) and [Federation](federation.md).