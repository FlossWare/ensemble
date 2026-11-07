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
| Vector store | Python `chromadb` | `services/embeddings/vector_store.py` imports `chromadb` and uses `PersistentClient`, persistent collections, cosine-space metadata; POC README identifies ChromaDB-backed exact search | Ensemble uses custom SQLAlchemy/SQLite vector tables in `ensemble/vector_store.py`; ChromaDB is not declared in `pyproject.toml` | **GAP: unapproved replacement. Restore ChromaDB.** |
| Embedding math | `numpy` | POC `services/embeddings/embed_lib.py` and `vector_service.py` | Declared as optional `local-embeddings` dependency and used by local embedding/vector paths | Present; verify supported deployment install profile |
| Tokenization | Hugging Face `tokenizers` | POC `embed_lib.py` loads the pinned tokenizer JSON using `Tokenizer.from_file` | Declared as optional `local-embeddings` and `context-tokenizer` dependency | Present; verify parity and install profile |
| Embedding inference | `onnxruntime` | POC `embed_lib.py` imports ONNX Runtime for default CPU FP32 inference | Declared as optional `local-embeddings` dependency | Present; verify pinned/runtime matrix |
| Embedding inference | `openvino` | POC `embed_lib.py` supports explicit CPU/GPU backends and fail-closed GPU selection | Declared as optional `openvino` dependency | Present; verify hardware runtime matrix |
| Reranking | `sentence-transformers` / `CrossEncoder` | POC `services/embeddings/reranking_service.py` loads the pinned local cross-encoder | Declared as optional `reranking` dependency | Present; local runtime verification pending |
| POC analytics / learning | `numpy`, `scikit-learn`, `pandas`, `joblib`, `pyyaml` | `core/requirements.txt` | Not declared in Ensemble's main or optional dependencies (except NumPy is available through local embeddings) | **Audit each active POC capability and its imports before disposition.** |
| POC integration package | `@xenova/transformers`, `better-sqlite3`, `chromadb` (Node client), `neo4j-driver`, `pg`, `uuid`, `js-yaml` | `core/package.json` and `core/package-lock.json` | Not declared in Ensemble, which is Python-only | **Scope and capability mapping required.** Do not silently classify as irrelevant. |
| POC Python integration package | `psycopg2-binary` | `core/requirements.txt` | Not declared in Ensemble | Existing Ensemble persistence boundary is SQLite-only; retain this as an explicit architecture exception pending user confirmation, not an accidental omission. |

## Important distinctions

- The Python ChromaDB dependency used by the POC vector service is distinct from the Node.js `chromadb` client listed in `core/package.json`. Both must be inventoried accurately.
- The POC's vector service uses ChromaDB's persistent client and collection model. Reimplementing collection operations in SQLite does not preserve the POC library.
- POC `core/` includes a broader Claude Code integration/runtime with Node and Python dependencies. Each dependency must be mapped to the POC capability that actually imports it; package presence alone does not prove that every dependency belongs in the standalone Ensemble server.
- Existing explicit Ensemble constraints (one Python application, SQLite persistence, no PostgreSQL) must be called out as explicit exceptions and reconciled with the POC inventory. They must not be used to disguise an unreviewed replacement.
- Transitive dependencies, exact installed versions, optional extras, platform markers, and lockfile reproducibility have not yet been fully reconciled.

## Required next audit steps

1. Recursively enumerate imports and dependency manifests in all POC service, core, tools, and evaluation code.
2. Map every direct dependency to the POC modules and tests that exercise it.
3. Compare against Ensemble's declared dependencies, optional extras, imports, runtime selection, and tests.
4. Classify each entry as same library, missing, explicit approved exception, POC-only integration dependency, or not yet assessed.
5. Restore the ChromaDB-backed vector implementation and its persistent collection semantics before claiming vector parity.
6. Add a CI check that fails when a POC runtime dependency is absent from Ensemble's dependency inventory without an explicit approved exception.

No dependency substitution is accepted merely because a replacement has a similar API.
