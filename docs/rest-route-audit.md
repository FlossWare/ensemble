# REST Route Inventory and Naming Audit

**Status:** Working inventory for the capability-first REST convention.
**Branch:** `parity/poc-capability-inventory`
**Purpose:** Record the current public route surface, identify inconsistent naming, and sequence cleanup without making speculative breaking changes.

## 1. Governing rule

Paths describe caller-facing capabilities and resources. The first path segment identifies the capability or resource; later segments identify a strategy, scope, representation, or stable resource identifier.

HTTP methods express ordinary create/read/replace/update/delete behavior. Explicit action endpoints remain appropriate for meaningful computations or domain actions that do not map naturally to CRUD. Paths must not expose internal tables, implementation classes, storage engines, or provider names.

A canonical route has one name. When it changes, update server routes, all clients, Control Center, docs, and existing tests, then remove the old route. Do not retain compatibility aliases.

## 2. Canonical decisions made

| Capability | Canonical route(s) | Decision |
|---|---|---|
| Default retrieval | `POST /api/v1/search` | Default search contract under the Search capability |
| Lexical retrieval | `POST /api/v1/search/lexical` | Explicit lexical-only strategy beneath Search |
| Lexical search status | `GET /api/v1/search/lexical/health`, `GET /api/v1/search/lexical/ready` | Status endpoints live beneath the capability |
| Unified Hybrid retrieval | `POST /api/v1/search/hybrid` | Search capability first, retrieval strategy second |
| Knowledge-scoped Hybrid retrieval | `POST /api/v1/search/hybrid/knowledge` | Keep only as a genuinely distinct caller-facing scope |
| Hybrid search status | `GET /api/v1/search/hybrid/health`, `GET /api/v1/search/hybrid/ready` | Status endpoints live beneath the capability |
| Source ingestion | `POST /api/v1/ingest/documents`, `/files`, `/urls`, `/transcripts`, `/sessions` | One ingestion namespace, source type as the resource |
| URL preview | `POST /api/v1/ingest/urls/preview` | Preview is subordinate to the URL source resource |

The previous Hybrid search routes were removed rather than aliased. Control Center and existing API/UI tests were updated to use the canonical search paths.

## 3. Current route inventory

### Platform and operations

- `GET /health`
- `GET /ready`
- `GET /api/v1/system/config`

### Ingestion

- `POST /api/v1/ingest/documents`
- `POST /api/v1/ingest/files`
- `POST /api/v1/ingest/urls/preview`
- `POST /api/v1/ingest/urls`
- `POST /api/v1/ingest/transcripts`
- `POST /api/v1/ingest/sessions`

### Search and retrieval

- `POST /api/v1/search`
- `GET /api/v1/search/lexical/health`
- `GET /api/v1/search/lexical/ready`
- `POST /api/v1/search/lexical`
- `POST /api/v1/search/hybrid`
- `POST /api/v1/search/hybrid/knowledge`
- `GET /api/v1/search/hybrid/health`
- `GET /api/v1/search/hybrid/ready`

### Embeddings and vector operations

- `GET /api/v1/embeddings/health`
- `GET /api/v1/embeddings/model`
- `POST /api/v1/embeddings/embed`
- `GET /api/v1/vector/health`
- `GET /api/v1/vector/collections`
- `POST /api/v1/vector/collections/{name}/records/lookup`
- `PUT /api/v1/vector/collections/{name}/records`
- `PATCH /api/v1/vector/collections/{name}/records/metadata`
- `POST /api/v1/vector/collections/{name}/records/prune`
- `POST /api/v1/vector/collections/{name}/query`

### Processing and ranking

- Chunking: `GET /api/v1/chunking/health`, `/ready`, `/profiles`; `POST /api/v1/chunking/chunk`
- Tokenization: `GET /api/v1/tokenization/health`, `/ready`, `/profiles`; `POST /api/v1/tokenization/tokenize`
- Entities: `GET /api/v1/entities/health`, `/ready`; `POST /api/v1/entities/extract`, `/resolve`, `/link`
- Intent: `GET /api/v1/intent/health`, `/ready`; `POST /api/v1/intent/classify`
- Ranking: `GET /api/v1/ranking/health`, `/ready`; `POST /api/v1/ranking/rank`
- Reranking: `GET /api/v1/reranking/health`, `/ready`; `POST /api/v1/reranking/rerank`

### Index and Knowledge

- Index: `GET /api/v1/index/health`, `/ready`, `/manifest`, `/sources/{source_id}`
- Index mutations: `POST /api/v1/index/revisions`, `/tombstones`
- Knowledge: `GET /api/v1/knowledge/health`, `/ready`, `/records/{source_id}`
- Knowledge mutations: `POST /api/v1/knowledge/records`, `/records/{source_id}/tombstone`

### Memory

- `GET /api/v1/memory/health`, `/ready`
- `GET /api/v1/memory/memories`, `/memories/{name}`, `/memories/{name}/entries`
- `PUT /api/v1/memory/memories/{name}`, `/memories/{name}/entries/{event_id}`
- `POST /api/v1/memory/memories/{name}/entries`, `/memory/recall`, `/memory/chunking`, `/memory/events`
- `POST /api/v1/memory/search/keywords`, `/search/semantic`, `/search/hybrid`
- `POST /api/v1/memory/sources/import`, `/sources/reconcile`; `GET /sources/status`

### Graph

- `GET /api/v1/graph/health`
- `POST /api/v1/graph/nodes`, `/edges`, `/query`, `/traverse`
- `GET /api/v1/graph/nodes/{node_id}`

### Learning, Thompson, and optimization

- Learning: `GET /api/v1/learning/health`, `/ready`, `/ping`, `/outcomes`, `/outcomes/{task_id}`, `/report`, `/artifacts`
- Learning mutations: `POST /api/v1/learning/outcomes`, `/artifacts`, `/reset`, and `/api/v1/learning`
- Thompson: `GET /api/v1/thompson/health`, `/ready`, `/ping`, `/state`
- Thompson mutations: `POST /api/v1/thompson/select`, `/outcomes`, `/reset`, and `/api/v1/thompson`
- GA: `GET /api/v1/ga/health`, `/ready`, `/optimizers`, `/runs`, `/runs/{run_id}`
- GA mutations: `POST /api/v1/ga/runs`, `/runs/{run_id}/promote`

### Remaining Hybrid orchestration routes

- `POST /api/v1/ingest/sessions`
- `POST /api/v1/memory/events`

Prompt capture now uses `POST /api/v1/memory/events`. Structured session ingestion uses `POST /api/v1/ingest/sessions`, alongside the other source ingestion operations.

## 4. Audit findings and proposed direction

| Area | Current shape | Finding | Next decision |
|---|---|---|---|
| Search | `/search`, `/search/lexical`, `/search/hybrid` | Three related contracts with distinct caller intent and response shape | Retain the default, lexical-only, and enriched Hybrid contracts under one Search namespace |
| Ingestion | `/ingest/*` | Source ingestion now shares one namespace, including structured sessions | Complete ingestion route consistency and decide whether lexical document ingestion is still needed as a separate route |
| Vector | Collection routes use record resources for bulk upsert, lookup, metadata updates, and pruning; query remains an explicit vector operation | Resource operations are now consistent; pruning is an explicit maintenance action | Review whether pruning should remain a public maintenance operation or be folded into source lifecycle orchestration |
| Graph | `nodes`, `edges`, `nodes/{id}`, `query`, `traverse` | Resource paths now consistent; graph computations remain explicit operations | Node and edge mutations now use `/graph/nodes` and `/graph/edges`, with reads at `/graph/nodes/{node_id}`; query/traversal remain explicit operations |
| Memory | Named memories, nested entries, search strategies, source imports/reconciliation, recall, events | Resource model is explicit; source lifecycle is being unified with Index and document retrieval | Review source identity and event lifecycle semantics |
| Context | `GET /api/v1/context/health`, `/ready`, `POST /api/v1/context/assemble` | New bounded cross-capability assembly contract; token count is explicitly approximate and compression is extractive | Add exact tokenizer integration only when a concrete model/tokenizer contract is selected |
| Knowledge / Index | Records, sources, revisions, tombstones | Mostly resource-oriented; tombstone is represented differently in the two groups | Decide one consistent tombstone lifecycle and preserve its revision semantics |
| Learning / Thompson | Outcomes and runs are resources; reset/select/report/ping/root POST are mixed | Some operations are resources, others are action verbs or duplicate status calls | Keep meaningful domain actions; remove redundant ping if health/readiness fully covers it; clarify root POST operations |
| GA | Runs and optimizers are resources; promote is an action | Mostly coherent | Keep promotion as an explicit domain action unless promotion records become first-class resources |
| Processing capabilities | `chunk`, `tokenize`, `classify`, `rank`, `rerank`, `embed`; entity operations under `/entities` | Computational operations remain explicit; durable canonical entity identity and relationships belong to Graph | Keep extraction stateless, resolution identity-oriented, and linking graph-backed |
| Configuration | `GET /api/v1/system/config` | Current implementation returns persistence root and database URL, exposing implementation details contrary to the contract principles | Reduce to caller-relevant, non-sensitive runtime configuration; keep storage internals private |

## 5. Search contract semantics

- `POST /api/v1/search`: default retrieval contract; returns the standard result collection.
- `POST /api/v1/search/lexical`: lexical-only retrieval; returns lexical results and retrieval metadata.
- `POST /api/v1/search/hybrid`: Hybrid orchestration; includes retrieval diagnostics, intent enrichment, and related-session context when available.
- `POST /api/v1/search/hybrid/knowledge`: Knowledge-scoped Hybrid retrieval.

These contracts share implementation components but are not aliases: their response contracts and caller intent differ.

## 6. Recommended cleanup sequence

1. **Search semantics:** complete. Default, lexical-only, and enriched Hybrid retrieval now live under the Search namespace with explicit distinctions.
2. **Ingestion:** structured session ingestion moved to `/ingest/sessions`; finish evaluating the lexical document convenience route and prompt-event placement.
3. **Graph:** node and edge resources moved to plural resource paths; query and traversal remain explicit graph operations.
4. **Vector:** collection record operations now use PUT/PATCH and resource-oriented paths; review the lifecycle ownership of pruning.
5. **Memory:** named-memory and entry resources, search strategies, source import/reconciliation, recall, and event capture now have canonical paths; next review source identity and event lifecycle semantics.
6. **Knowledge and Index:** align tombstone lifecycle semantics.
7. **Learning and Thompson:** normalize state, outcomes, reports, and domain actions.
8. **Configuration and operational status:** remove storage leakage and make capability health/readiness coverage consistent.

## 7. Change discipline

For each route family:
- inspect the request/response contract and actual consumer intent;
- choose canonical paths before editing implementation;
- update server, Control Center, client references, docs, and existing tests in the same change;
- remove old routes rather than adding aliases;
- run the existing relevant tests and CI when available;
- do not expand the test suite merely to increase test count while functionality-first work is in progress.

This is an audit, not authorization to mechanically rename every action endpoint. A route changes only when the caller-facing resource model and operation semantics are clear.


## 8. Intent and entity capability decisions

- Intent classification is a standalone capability, not Search-owned logic. It returns a primary intent plus additional detected intents, evidence spans, heuristic confidence, constraints, and required capability hints.
- Entity extraction is stateless and returns typed candidates with normalized values, offsets, confidence, and provenance.
- Entity resolution is separate from extraction. It uses deterministic normalized identity matching and Graph-backed canonical entity nodes; ambiguous matches are surfaced, never guessed.
- Entity linking is separate from resolution. It creates typed Graph edges only between canonical entity nodes.
- Hybrid Search may consume intent and extraction, but it does not silently persist query entities or create graph relationships.
- Model-backed classifiers, fuzzy resolution, ontology management, and learned entity extraction remain future enhancements behind these contracts. They are not represented as implemented.
