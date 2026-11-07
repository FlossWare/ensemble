# POC Dependency Parity Audit

Reference: `FlossWare/poc-claude-ensemble` (`main`)
Target: `FlossWare/ensemble` (`integration/source-lifecycle-mainline-2026-11-04`)

## Acceptance rule

Ensemble preserves the POC's third-party libraries and their proven roles. A library substitution is a parity gap, even when a replacement exposes similar routes or operations. A substitution requires a written technical justification and explicit approval before it is accepted.

The one-application/one-listener REST architecture does not authorize replacing working storage, model, inference, parsing, retrieval, or analytics libraries. Dependencies can be consolidated behind Ensemble capability boundaries without changing the underlying library.

## Initial dependency inventory

This is a source-backed first pass, not a completed transitive dependency lock audit. It separates the POC's service implementation from the broader `core/` Claude Code integration package so that dependencies are not accidentally attributed to the wrong runtime.

| POC component | Library / runtime | Evidence in POC | Ensemble status | Parity disposition |
|---|---|---|---|---|
| Vector store | Python `chromadb` | `services/embeddings/vector_store.py` imports `chromadb` and uses `PersistentClient`, persistent collections, cosine-space metadata; POC README identifies ChromaDB-backed exact search | Production API now selects `ChromaVectorStore` (`ensemble/chroma_vector_store.py`); ChromaDB is a required runtime dependency. Legacy `SqliteVectorStore` remains for direct adapter tests, not production selection | **Library restored; Chroma lifecycle and generation-mirror tests pass. POC corpus import, production recall, and performance verification remain.** |
| Embedding math | `numpy` | POC `services/embeddings/embed_lib.py` and `vector_service.py` | Declared as optional `local-embeddings` dependency and used by local embedding/vector paths | Present; verify supported deployment install profile |
| Tokenization | Hugging Face `tokenizers` | POC `embed_lib.py` loads the pinned tokenizer JSON using `Tokenizer.from_file` | Declared as optional `local-embeddings` and `context-tokenizer` dependency | Present; verify parity and install profile |
| Embedding inference | `onnxruntime` | POC `embed_lib.py` imports ONNX Runtime for default CPU FP32 inference | Declared as optional `local-embeddings` dependency | Present; verify pinned/runtime matrix |
| Embedding inference | `openvino` | POC `embed_lib.py` supports explicit CPU/GPU backends and fail-closed GPU selection | Declared as optional `openvino` dependency | Present; verify hardware runtime matrix |
| Reranking | `sentence-transformers` / `CrossEncoder` | POC `services/embeddings/reranking_service.py` loads the pinned local cross-encoder | Declared as optional `reranking` dependency | Present; local runtime verification pending |
| Graph | `networkx` | POC `services/graph-service/graph_service.py` uses `networkx.MultiDiGraph` for durable graph operations and traversal | Required dependency; `ensemble/graph_store.py` constructs a `MultiDiGraph` for typed, directional traversal while SQLite remains durable persistence | Retained; graph regression tests pass |
| POC analytics / learning | `numpy`, `scikit-learn`, `pandas`, `joblib`, `pyyaml`, `filelock`, `Pillow` | `core/requirements.txt` plus its explicit note that `filelock` and Pillow are handled in API requirements | NumPy is present through local embedding extras; scikit-learn, pandas, joblib, PyYAML, filelock, and Pillow are now declared in the `poc-learning` extra | Libraries retained in install metadata; map each to the Ensemble GA/Learning/ingestion code and add capability-level tests before claiming behavior parity |
| POC integration package | `@xenova/transformers`, `better-sqlite3`, `chromadb` (Node client), `neo4j-driver`, `pg`, `uuid`, `js-yaml` | `core/package.json` and `core/package-lock.json` | Non-PostgreSQL dependencies are retained in optional `integrations/claude-code/package.json`; `pg` is deliberately excluded under the explicit SQLite-only architecture constraint | **Dependency manifest parity retained; actual host-hook porting and use audit remain separate work.** |
| POC Python integration package | `psycopg2-binary` | `core/requirements.txt` | Not declared in Ensemble | Existing Ensemble persistence boundary is SQLite-only; retain this as an explicit architecture exception pending user confirmation, not an accidental omission. |

## Important distinctions

- The Python ChromaDB dependency used by the POC vector service is distinct from the Node.js `chromadb` client listed in `core/package.json`. Both must be inventoried accurately. The Node package also declares `@xenova/transformers`, `better-sqlite3`, `neo4j-driver`, `uuid`, and `js-yaml`; these are retained in the optional host-adapter manifest, not loaded by the Python REST server. POC source inspection found `js-yaml` used by the model config loader and code-documentation tools, the Node `chromadb` package imported by the legacy memory RAG hook, and `pg` used by the PostgreSQL workflow-storage integration. `@xenova/transformers`, `better-sqlite3`, `neo4j-driver`, and `uuid` appear in the POC package manifest but were not found in the 43 tracked core JavaScript files inspected; they remain declared pending a broader source audit. The `pg` package remains excluded from Ensemble because PostgreSQL is explicitly out of scope.
- The POC's vector service uses ChromaDB's persistent client and collection model. Production Ensemble now uses ChromaDB for vector records and similarity search; SQLite remains the transactional authority for source/index/generation metadata, not a replacement vector engine.
- POC `core/` includes a broader Claude Code integration/runtime with Node and Python dependencies. Each dependency must be mapped to the POC capability that actually imports it; package presence alone does not prove that every dependency belongs in the standalone Ensemble server.
- Existing explicit Ensemble constraints (one Python application, SQLite persistence, no PostgreSQL) are explicit architecture constraints. POC PostgreSQL clients (`pg`, `psycopg2-binary`) are excluded from the Ensemble runtime because PostgreSQL is explicitly out of scope; this is not permission to replace unrelated working libraries.
- Transitive dependencies, exact installed versions, optional extras, platform markers, and lockfile reproducibility have not yet been fully reconciled.

## Required next audit steps

1. Recursively enumerate imports and dependency manifests in all POC service, core, tools, and evaluation code.
2. Map every direct dependency to the POC modules and tests that exercise it.
3. Compare against Ensemble's declared dependencies, optional extras, imports, runtime selection, and tests.
4. Classify each entry as same library, missing, explicit approved exception, POC-only integration dependency, or not yet assessed.
5. Verify ChromaDB persistent collection compatibility, cosine scoring, partition isolation, source tombstones, generation mirroring/cutover, and migration behavior before claiming vector parity. The first CI suite now covers collection persistence, metadata, source lifecycle, partition isolation, and the DocumentService generation-mirror path; see [ChromaDB storage and migration](chroma-migration.md).
6. Add a CI check that fails when a POC runtime dependency is absent from Ensemble's dependency inventory without an explicit approved exception.

No dependency substitution is accepted merely because a replacement has a similar API.
