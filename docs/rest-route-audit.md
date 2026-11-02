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
| Unified Hybrid retrieval | `POST /api/v1/search/hybrid` | Search capability first, retrieval strategy second |
| Knowledge-scoped Hybrid retrieval | `POST /api/v1/search/hybrid/knowledge` | Keep only as a genuinely distinct caller-facing scope |
| Hybrid search status | `GET /api/v1/search/hybrid/health`, `GET /api/v1/search/hybrid/ready` | Status endpoints live beneath the capability |
| Source ingestion | `POST /api/v1/ingest/documents`, `/files`, `/urls`, `/transcripts` | One ingestion namespace, source type as the resource |
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

### Search and retrieval

- `POST /api/v1/search`
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
- Extraction: `GET /api/v1/extraction/health`, `/ready`; `POST /api/v1/extraction/extract`
- Intent: `GET /api/v1/intent/health`, `/ready`; `POST /api/v1/intent/classify`
- Ranking: `GET /api/v1/ranking/health`, `/ready`; `POST /api/v1/ranking/rank`
- Reranking: `GET /api/v1/reranking/health`, `/ready`; `POST /api/v1/reranking/rerank`

### Index and Knowledge

- Index: `GET /api/v1/index/health`, `/ready`, `/manifest`, `/sources/{source_id}`
- Index mutations: `POST /api/v1/index/revisions`, `/tombstones`
- Knowledge: `GET /api/v1/knowledge/health`, `/ready`, `/records/{source_id}`
- Knowledge mutations: `POST /api/v1/knowledge/records`, `/records/{source_id}/tombstone`

### Memory

- `GET /api/v1/memory/health`, `/ready`, `/list`, `/knowledge/status`
- `POST /api/v1/memory/read`, `/write`, `/append`, `/append-once`, `/entries`, `/retrieve`, `/search`, `/search-semantic`, `/search-hybrid`, `/chunk`, `/ingest`, `/reconcile`

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
- `POST /api/v1/hybrid/prompts`

Prompt capture is not a search operation and remains under review against the Memory event/resource model. Structured session ingestion is now `POST /api/v1/ingest/sessions`, alongside the other source ingestion operations. Do not place ingestion routes under a generic Hybrid prefix.

## 4. Audit findings and proposed direction

| Area | Current shape | Finding | Next decision |
|---|---|---|---|
| Search | `/search`, `/search/lexical`, `/search/hybrid` | Three related contracts with distinct caller intent and response shape | Retain the default, lexical-only, and enriched Hybrid contracts under one Search namespace |
| Ingestion | `/ingest/*` | Source ingestion now shares one namespace, including structured sessions | Complete ingestion route consistency and decide whether lexical document ingestion is still needed as a separate route |
| Vector | Collection routes now use record resources for bulk upsert, lookup, metadata updates, and pruning; query remains an explicit vector operation | Mixes resource operations and implementation-flavored verbs | Bulk record replacement uses PUT; record lookup and query remain explicit operations; metadata updates use PATCH; pruning remains a named maintenance action whose lifecycle semantics need review |
| Graph | `nodes`, `edges`, `nodes/{id}`, `query`, `traverse` | Resource paths now consistent; graph computations remain explicit operations | Node and edge mutations now use `/graph/nodes` and `/graph/edges`, with reads at `/graph/nodes/{node_id}`; query/traversal remain explicit operations |
| Memory | Mixed `read`, `write`, `append`, `entries`, `retrieve`, `list`, and three search paths | No single resource model; semantic strategy appears in some paths but not others | Define Memory resources and operation semantics before renaming; avoid mechanical string replacement |
| Knowledge / Index | Records, sources, revisions, tombstones | Mostly resource-oriented; tombstone is represented differently in the two groups | Decide one consistent tombstone lifecycle and preserve its revision semantics |
| Learning / Thompson | Outcomes and runs are resources; reset/select/report/ping/root POST are mixed | Some operations are resources, others are action verbs or duplicate status calls | Keep meaningful domain actions; remove redundant ping if health/readiness fully covers it; clarify root POST operations |
| GA | Runs and optimizers are resources; promote is an action | Mostly coherent | Keep promotion as an explicit domain action unless promotion records become first-class resources |
| Processing capabilities | `chunk`, `tokenize`, `extract`, `classify`, `rank`, `rerank`, `embed` | These are computational operations, not durable CRUD resources | Explicit operation names are acceptable; keep capability roots and status suffixes consistent |
| Configuration | `GET /api/v1/system/config` | Current implementation returns persistence root and database URL, exposing implementation details contrary to the contract principles | Reduce to caller-relevant, non-sensitive runtime configuration; keep storage internals private |

## 5. Search contract semantics\n\n- `POST /api/v1/search`: default retrieval contract; returns the standard result collection.\n- `POST /api/v1/search/lexical`: lexical-only retrieval; returns lexical results and retrieval metadata.\n- `POST /api/v1/search/hybrid`: Hybrid orchestration; includes retrieval diagnostics, intent enrichment, and related-session context when available.\n- `POST /api/v1/search/hybrid/knowledge`: Knowledge-scoped Hybrid retrieval.\n\nThese contracts share implementation components but are not aliases: their response contracts and caller intent differ.\n\n## 6. Recommended cleanup sequence

1. **Search semantics:** complete. Default, lexical-only, and enriched Hybrid retrieval now live under the Search namespace with explicit distinctions.
2. **Ingestion:** structured session ingestion moved to `/ingest/sessions`; finish evaluating the lexical document convenience route and prompt-event placement.
3. **Graph:** node and edge resources moved to plural resource paths; query and traversal remain explicit graph operations.
4. **Vector:** define collection and record resource semantics, then replace action-heavy CRUD paths.
5. **Memory:** define the resource model and distinguish memory entries, events, source imports, reconciliation, and retrieval strategies.
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
