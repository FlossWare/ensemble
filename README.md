# Ensemble

Ensemble is a standalone, REST-first platform for reusable intelligence capabilities. It provides shared memory, knowledge, retrieval, and related capabilities to applications and AI clients.

Ensemble is the product and implementation boundary. It is not a Claude Code extension and does not depend on Claude, Codex, Cursor, Crush, MCP, or any other client to run.

## Architecture

- One application and one REST server initially, with multiple versioned endpoint groups.
- REST is the canonical contract for external consumers and internal capability communication.
- Capabilities are independently addressable through REST contracts, even when co-located in one deployment.
- Implementations are composed and decorated behind stable contracts.
- Providers are pluggable. Embedding and reranking implementations can be selected independently of the API contract.
- Retrieval is independently usable; generation is optional and provider-backed.
- MCP is an optional adapter over REST capabilities. It contains no duplicate business logic.
- Ensemble uses SQLite for its own persistence. Alternative persistence implementations and shared infrastructure belong to FlossWare proper.

See [Architecture and decisions](docs/architecture.md), [REST contract conventions](docs/rest-contracts.md), [REST route inventory and naming audit](docs/rest-route-audit.md), [POC capability inventory](docs/poc-parity.md), [functionality-first capability completion plan](docs/capability-completion-plan.md), [Control Center requirements](docs/control-center-requirements.md), [Control Center API mapping](docs/control-center-api-mapping.md), and [current implementation status](docs/implementation-status.md).

## Current implementation

The Python application provides health/readiness, durable document ingestion, versioned window/Markdown/message chunking with persisted provenance, SQLite FTS5 lexical indexing with shared tokenization and BM25, exact SQLite vector collections, Hybrid lexical/vector retrieval with degraded-mode fallback, optional cross-encoder reranking, intent enrichment, typed entity extraction, Graph-backed entity resolution/linking, and graph-based related-session lookup. It also includes SQLite-backed episodic Memory and Knowledge, Index revision/tombstone management, Graph, Thompson sampling, Learning outcomes/artifacts, a bounded GA optimizer, extraction, tokenization, and explainable ranking through one FastAPI application. Raw JSONL transcript ingestion supports caller-supplied incremental slices with UTF-8 byte checkpoints; bounded PDF uploads and public single-page HTML/plain-text URI ingestion feed the shared document pipeline. Durable transcript checkpoint jobs, broad website crawling, OCR, complete hybrid channel orchestration, and several deeper Memory/GA operational behaviors remain open in the [capability completion plan](docs/capability-completion-plan.md).

SQLite is the only persistence implementation in this repository. The storage adapter is private to the implementation; REST contracts expose neither database choice nor schema. Database schema changes are managed with Alembic migrations.

## Run locally

Python 3.12 or newer is required.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
alembic upgrade head
uvicorn ensemble.api:app --host 127.0.0.1 --port 8090
```

The default persistence root is .ensemble and the database is .ensemble/ensemble.db. Configure the HTTP listener with ENSEMBLE_HOST and ENSEMBLE_PORT, and move all default persistent data with ENSEMBLE_PERSISTENCE_ROOT. The server can also be started with command-line overrides. For example:

```bash
ENSEMBLE_PERSISTENCE_ROOT=/var/lib/ensemble alembic upgrade head
ENSEMBLE_PERSISTENCE_ROOT=/var/lib/ensemble python -m ensemble --host 127.0.0.1 --port 8090
```

The API refuses to start against an unmigrated or incomplete database. Apply migrations as a deployment step before starting the application.

Run the existing tests:

```bash
pytest
```

All source ingestion uses the canonical `/api/v1/ingest/...` namespace: documents, files, URLs (including preview), and transcripts. The browser Control Center is available at /control. It includes live capability health/readiness checks, a retrieval explorer with optional reranking, structured transcript-session ingestion, Index and Vector inspection, Memory and Knowledge inspection, source lookup and confirmed permanent tombstoning, URI preview/explicit-ingest, PDF upload ingestion, raw JSONL transcript ingestion, GA run/history/candidate inspection and approval-to-export, Thompson model inspection/selection/reset, and Learning report/outcome/artifact inspection with confirmed reset. GA candidates are not applied to live settings. Discovered URI links are displayed but not crawled. API documentation is available at /docs; the OpenAPI document is at /openapi.json.

## Scope

This repository contains the Ensemble platform only.

Client-specific integrations belong in separate consumer repositories (for example, ensemble-claude, ensemble-codex, ensemble-cursor, and ensemble-crush). The broader FlossWare ecosystem is not part of this repository's scope.

The historical claude-ensemble and poc-claude-ensemble repositories are reference material for capability discovery and useful behavior. They are not runtime dependencies or the target implementation. Their internal designs and test suites are not specifications that Ensemble must reproduce.

Existing POC services and data must remain undisturbed during this work. Ensemble must not couple itself to POC process internals, database files, or persistence directories. Any required data migration will use an explicitly designed, supported interface or export/import process.

## Runtime configuration

The HTTP listener and persistence root are configurable through ENSEMBLE_HOST, ENSEMBLE_PORT and ENSEMBLE_PERSISTENCE_ROOT. Defaults are 127.0.0.1, 8090 and ./.ensemble respectively. The SQLite database defaults to <persistence-root>/ensemble.db. Command-line overrides are available through python -m ensemble --host, --port, --persistence-root and --database-url. See docs/installation.md for the systemd installer and deployment configuration.
