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

See [Architecture and decisions](docs/architecture.md), [REST contract conventions](docs/rest-contracts.md), [POC capability parity](docs/poc-parity.md), and [current implementation status](docs/implementation-status.md).

## Current implementation

The Python application provides health/readiness, durable document ingestion, versioned chunking profiles, optional embeddings and local cross-encoder reranking, lexical/vector retrieval, reciprocal-rank fusion, tokenization, exact/uncertain entity extraction, explainable intent classification, and ranking through one FastAPI application.

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

The default database is .ensemble/ensemble.db. Override its SQLite URL with ENSEMBLE_DATABASE_URL, for example:

```bash
ENSEMBLE_DATABASE_URL='sqlite+aiosqlite:////var/lib/ensemble/ensemble.db' alembic upgrade head
ENSEMBLE_DATABASE_URL='sqlite+aiosqlite:////var/lib/ensemble/ensemble.db' uvicorn ensemble.api:app --host 127.0.0.1 --port 8090
```

The API refuses to start against an unmigrated or incomplete database. Apply migrations as a deployment step before starting the application.

Run tests:

```bash
pytest
```

API documentation is available at /docs; the OpenAPI document is at /openapi.json.

## Scope

This repository contains the Ensemble platform only.

Client-specific integrations belong in separate consumer repositories (for example, ensemble-claude, ensemble-codex, ensemble-cursor, and ensemble-crush). The broader FlossWare ecosystem is not part of this repository's scope.

The historical claude-ensemble and poc-claude-ensemble repositories are reference material for capability discovery, behavior, and contract compatibility. They are not runtime dependencies or the target implementation.

Existing POC services and data must remain undisturbed during this work. Ensemble must not couple itself to POC process internals, database files, or persistence directories. Any required data migration will use an explicitly designed, supported interface or export/import process.
