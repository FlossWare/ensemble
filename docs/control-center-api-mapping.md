# Control Center API and UI Mapping

**Status:** Initial inventory on `parity/poc-capability-inventory`.
**Purpose:** Record what the current REST application exposes and what the current browser Control Center actually uses. This is an implementation inventory, not a claim that every requirement is complete.

## 1. Current browser workbench coverage

| Workbench | Current browser behavior | REST operations used | Assessment |
|---|---|---|---|
| Platform status | Readiness summary | `GET /ready` | Basic status only; no history, metrics, or diagnostics |
| Capability Explorer | Health/readiness table | Capability `/health` and `/ready` endpoints | Useful baseline; mostly generic status |
| Retrieval Explorer | Hybrid search with optional reranking | `POST /api/v1/search/hybrid` | Does not yet compare independent channel results or expose every pipeline stage |
| Transcript ingestion | Submit structured session payload | `POST /api/v1/ingest/sessions` | Explicit mutation; needs richer preview/trace |
| Index and Vector | Manifest, collection list, source lookup | `GET /api/v1/index/manifest`, `GET /api/v1/vector/collections`, `GET /api/v1/index/sources/{source_id}` | Read-only inspection plus separate tombstone action |
| Memory and Knowledge | Memory names, Knowledge status, hybrid Memory search | `GET /api/v1/memory/memories`, `GET /api/v1/memory/sources/status`, `POST /api/v1/memory/search/hybrid` | Partial; no event/session detail workbench |
| URI ingestion | Preview then explicit ingest | `POST /api/v1/ingest/urls/preview`, `POST /api/v1/ingest/urls` | Preview/commit separation exists |
| GA | Run, history, detail, candidate promotion/export | `/api/v1/ga/runs`, `/api/v1/ga/runs/{run_id}`, `POST /api/v1/ga/runs/{run_id}/promote` | Initial workbench; synthetic evaluators must remain clearly labeled |
| Thompson | State, selection, reset | `GET /api/v1/thompson/state`, `POST /api/v1/thompson/select`, `POST /api/v1/thompson/reset` | Model-level state and operations; task/capability filtering is not implemented |
| Learning | Report, recent outcomes, artifacts, confirmed reset | `GET /api/v1/learning/report`, `GET /api/v1/learning/outcomes`, `GET /api/v1/learning/artifacts`, `POST /api/v1/learning/reset` | Initial workbench |
| Runtime configuration | Display process configuration | `GET /api/v1/system/config` | Must continue redacting secrets and avoid implying hot reload |

## 2. REST capability inventory

The following endpoint groups were found in `ensemble/api.py` at the time of this inventory.

| Capability | Existing REST surface | Current Control Center depth |
|---|---|---|
| System/operations | `/health`, `/ready`, `/api/v1/system/config` | Basic |
| Embeddings | `/api/v1/embeddings/health`, `/model`, `POST /embed` | Health only |
| Chunking | `/api/v1/chunking/health`, `/ready`, `/profiles`, `POST /chunk` | Health only |
| Tokenization | `/api/v1/tokenization/health`, `/ready`, `/profiles`, `POST /tokenize` | Health only |
| Extraction | `/api/v1/extraction/health`, `/ready`, `POST /extract` | Health only |
| Intent | `/api/v1/intent/health`, `/ready`, `POST /classify` | Health only |
| Ranking | `/api/v1/ranking/health`, `/ready`, `POST /rank` | Health only |
| Reranking | `/api/v1/reranking/health`, `/ready`, `POST /rerank` | Health only; hybrid query can opt in to reranking |
| Vector | `/api/v1/vector/health`, collection list, collection existing/upsert/metadata/delete-superseded/query | Collection list only |
| Lexical | `/api/v1/search/lexical/health`, `/ready`, document write, search | Health/readiness only; retrieval is not separately compared in UI |
| Hybrid | `/api/v1/search/hybrid/health`, `/ready`, search, knowledge search, session ingest, prompt capture | Hybrid search and session ingestion |
| Index | `/api/v1/index/health`, `/ready`, revision decisions, tombstones, source lookup, manifest | Manifest/source inspection and tombstone |
| Knowledge | `/api/v1/knowledge/health`, `/ready`, record write/read, tombstone | Indirect status only |
| Memory | `/api/v1/memory/*` health/readiness, read/write/append, entries, list, search, retrieve, chunk, semantic/hybrid search, ingest, reconcile, Knowledge status | Names/status/search only |
| Graph | `/api/v1/graph/health`, node/edge mutation, node lookup, query, traversal | Health only |
| Thompson | `/api/v1/thompson/*` health/readiness/state/select/outcomes/reset | State/select/reset |
| GA | `/api/v1/ga/*` health/readiness/optimizers/runs/detail/promote | Run/detail/promote |
| Learning | `/api/v1/learning/*` health/readiness/outcomes/report/artifacts/reset and outcome workflow | Report/outcomes/artifacts/reset |
| Scraping | `POST /api/v1/ingest/urls/preview`, `POST /api/v1/ingest/urls` | Preview/ingest |

## 3. Implemented in the current parity branch

The Control Center now includes initial read-only workbenches backed by existing REST contracts:

- **Graph:** property-filtered node queries, node lookup, and bounded traversal.
- **Chunking:** profile inventory and non-persistent chunk previews.
- **Tokenization:** analyzer profile inventory and token previews.
- **Knowledge:** record lookup by source identifier.
- **Pipeline Inspector (diagnostic preview):** sequentially invokes current Tokenization, Extraction, Intent, and Chunking operations and reports each stage's result or failure.

The diagnostic pipeline is deliberately labeled as an interactive preview. It does not mutate persistence and does not claim to be a trace of a real ingestion or retrieval execution. It currently does not include embedding, vector writes, lexical writes, fusion, or reranking.

These additions are UI integration only; they do not establish POC parity for the underlying capability implementations. Browser behavior and API contract tests still need to be run.

## 4. Priority gaps against accepted requirements

### P0: Build a truthful read-only foundation

- Add capability-specific statistics/status responses where current health/readiness responses are insufficient.
- Include Graph in a useful read-only workbench: node/edge counts, bounded node inspection, query, and traversal.
- Add Chunking profile inventory and preview, using the existing `/profiles` and `/chunk` contracts.
- Add Tokenization profile inventory and token preview, using existing contracts.
- Add Vector collection detail and bounded record/query inspection. Do not present exact scan as ANN.
- Add Knowledge record/source inspection rather than relying only on Memory's Knowledge status.
- Add explicit unsupported/unavailable states; do not fabricate metrics.

### P1: Explain processing and retrieval

- Add Embeddings model/provider status and a controlled text embedding probe.
- Add Extraction and Intent workbenches that expose confidence/evidence and rule-based semantics.
- Add independent Lexical and Vector query inspection, followed by Hybrid comparison.
- Display RRF contributions and candidate lineage. The current ranking endpoint is independently callable, but the current Hybrid response must be inspected to determine whether it exposes enough intermediate detail.
- Display reranking input/output and whether reranking ran or degraded.
- Add a pipeline inspector for ingestion and query execution. Existing endpoints are capability operations, not yet a correlated end-to-end trace contract.

### P2: Operational depth

- Add Graph and Memory recent-activity summaries if the service contracts can provide them without unbounded scans.
- Add operational metrics only when measured at the backend, with units and time windows.
- Add job history/progress only when durable job semantics exist; do not fake background-job status for synchronous requests.
- Add safe database/migration diagnostics without exposing private schema details or secrets.
- Add controlled maintenance operations only after scope, impact, confirmation, and audit semantics are defined.

## 5. Pipeline Inspector contract gap

The current API exposes the individual operations needed for several stages, but the inspected route inventory does not show a general pipeline-run or trace resource that correlates them.

A genuine end-to-end inspector therefore needs an explicit contract decision. Two reasonable increments:

1. **Immediate, no new trace persistence:** the browser runs existing preview/probe operations and labels them as an interactive diagnostic sequence. Each stage result is real, but the sequence is not represented as a durable server-side trace. It must not imply that it inspected a prior ingestion or search execution.
2. **Later, correlated execution traces:** add a server-owned trace/run identifier and structured stage events to ingestion and retrieval responses. Define payload redaction and retention before persisting trace details.

Prefer the first increment for capability previews and the second only when the actual pipeline execution can emit trustworthy stage data. Do not reconstruct a supposed historical trace by rerunning a query or document through current settings.

## 6. Immediate implementation order

1. Add Graph, Chunking, Tokenization, and Knowledge read-only workbenches from existing endpoints.
2. Add Lexical/Vector/Hybrid comparison with explicit channel provenance and truthful limits.
3. Add Embeddings, Extraction, Intent, Ranking, and Reranking diagnostic workbenches.
4. Implement the interactive Pipeline Inspector for preview/probe flows, clearly marked as a new diagnostic run.
5. Define correlated server-side pipeline traces for actual ingestion and retrieval execution.
6. Add backend-owned operational statistics and carefully scoped maintenance only where justified.

## 7. Inventory caveats

- This inventory was derived from the current `ensemble/api.py`, `ensemble/control_panel.html`, and `ensemble/control.js` on the parity branch.
- Endpoint existence does not establish complete POC parity, production readiness, or suitability for an administrative UI.
- Some endpoint groups include mutation operations. The Control Center should expose them only when explicitly required and protected by the accepted interaction model.
- The route list and UI will evolve; update this document when endpoint contracts or workbench coverage change.


## Intent and entities

- Intent preview: `POST /api/v1/intent/classify`
- Entity extraction preview: `POST /api/v1/entities/extract`
- Explicit canonical identity resolution: `POST /api/v1/entities/resolve`
- Explicit canonical relationship creation: `POST /api/v1/entities/link`
- Entity capability status: `GET /api/v1/entities/health` and `GET /api/v1/entities/ready`

The Entity Workbench keeps extraction read-only. Resolution and linking require separate operator actions because they persist canonical nodes and Graph edges.
