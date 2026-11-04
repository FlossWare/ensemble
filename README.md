# Ensemble

Ensemble is a standalone, REST-first platform for reusable intelligence capabilities. It provides shared Memory, Knowledge, Graph, ingestion, indexing, retrieval, and context capabilities to applications and AI clients.

Ensemble is the product and implementation boundary. It does not depend on any client application or optional model provider to run.

## Architecture

- One application and one REST server initially, with multiple versioned endpoint groups.
- REST is the canonical contract for external consumers and capability-to-capability communication.
- Capabilities are independently addressable through REST contracts, even when co-located in one deployment.
- Implementations are composed and decorated behind stable contracts.
- Providers are pluggable; embedding and ranking implementations do not define the API contract.
- Retrieval is independently usable; generation is optional and provider-backed.
- MCP is an optional adapter over REST capabilities. It contains no duplicate business logic.
- Ensemble uses SQLite for its own persistence. Alternative persistence implementations and shared infrastructure belong to FlossWare proper.

The REST boundary is the architectural rule. The current review implementation still has orchestration paths that call stores and services directly; those paths must be moved behind internal REST clients before claiming full capability-boundary compliance. See [Architecture](docs/architecture.md) and [review readiness](docs/review-readiness.md).

See [REST contract conventions](docs/rest-contracts.md), [REST route inventory and naming audit](docs/rest-route-audit.md), [capability completion plan](docs/capability-completion-plan.md), [Control Center requirements](docs/control-center-requirements.md), [Control Center API mapping](docs/control-center-api-mapping.md), and [current implementation status](docs/implementation-status.md).

## Current implementation

The Python application provides health/readiness, durable document ingestion, versioned window/Markdown/message chunking with persisted provenance, SQLite FTS5 lexical indexing with shared tokenization and BM25, exact SQLite vector collections, Hybrid lexical/vector retrieval with degraded-mode fallback, optional cross-encoder reranking, intent enrichment, typed entity extraction, Graph-backed entity resolution/linking, and graph-based related-session lookup. It also includes SQLite-backed episodic Memory with append-once conflict detection, expiry-aware recall and explicit deletion, plus Knowledge, Index revision/tombstone management, Graph, Thompson sampling, Learning outcomes/artifacts, a bounded GA optimizer, extraction, tokenization, and explainable ranking through one FastAPI application. Raw JSONL transcript ingestion supports caller-supplied incremental slices with UTF-8 byte checkpoints; bounded PDF uploads and public single-page HTML/plain-text URI ingestion feed the shared document pipeline. Durable transcript checkpoint jobs, bounded robots-aware same-origin crawling with persistent crawl/ingestion jobs, optional exact context-tokenizer budgeting, OCR, scheduled source refresh, and several deeper Memory/GA operational behaviors are tracked in the [capability completion plan](docs/capability-completion-plan.md).

SQLite is the only persistence implementation in this repository. The storage adapter is private to the implementation; REST contracts expose neither database choice nor schema. Database schema changes are managed with Alembic migrations.

## Run locally

Python 3.12 or newer is required.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
# Optional, for exact model-tokenizer context budgets:
python -m pip install -e '.[context-tokenizer]'
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

All source ingestion uses the canonical /api/v1/ingest/... namespace: documents, files, URLs (including preview), and transcripts. The browser Control Center is available at /control. It includes live capability health/readiness checks, a retrieval explorer with optional reranking, structured transcript-session ingestion, Index and Vector inspection, Memory and Knowledge inspection, source lookup and confirmed permanent tombstoning, bounded URI crawl preview plus durable queue/status/retry and explicit batch ingestion, PDF upload ingestion, raw JSONL transcript ingestion, GA run/history/candidate inspection and approval-to-export, Thompson model inspection/selection/reset, and Learning report/outcome/artifact inspection with confirmed reset. GA candidates are not applied to live settings. Discovered URI links are displayed but not crawled. API documentation is available at /docs; the OpenAPI document is at /openapi.json.

## Scope

This repository contains the Ensemble platform only.

Client-specific integrations belong in separate consumer repositories. The broader FlossWare ecosystem is not part of this repository's scope.

Ensemble has no runtime dependency on other applications' data stores or process internals. Any required import or migration must use an explicitly designed, supported interface or export/import contract. Data migration is separate from implementing Ensemble's capabilities.

## Runtime configuration

The HTTP listener and persistence root are configurable through ENSEMBLE_HOST, ENSEMBLE_PORT and ENSEMBLE_PERSISTENCE_ROOT. Defaults are 127.0.0.1, 8090 and ./.ensemble respectively. The SQLite database defaults to <persistence-root>/ensemble.db. Command-line overrides are available through python -m ensemble --host, --port, --persistence-root and --database-url. Set `ENSEMBLE_CONTEXT_TOKENIZER_PATH` to a local tokenizer JSON file and optionally `ENSEMBLE_CONTEXT_TOKENIZER_ID` to a safe model/tokenizer label to enable exact context token counting. The path is server configuration, not a client-supplied path. See docs/installation.md for the systemd installer and deployment configuration.
