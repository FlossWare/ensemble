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
- Storage technology and migration mechanics are implementation decisions, not public API contracts.

See [Architecture and decisions](docs/architecture.md), [REST contract conventions](docs/rest-contracts.md), and [current implementation status](docs/implementation-status.md).

## Current implementation

The Python application provides health/readiness, durable document ingestion, chunking, optional embeddings, partition-scoped memory and graph capabilities, Unicode tokenization, token-window chunking, provider-backed reranking integrated into search, bounded lexical and vector candidate retrieval, PostgreSQL pgvector HNSW indexing, and reciprocal-rank fusion through one FastAPI application.

SQLite is the default local database. PostgreSQL is supported for a shared deployment. The storage adapter is private to the implementation; REST contracts expose neither database choice nor schema. Database schema changes are managed with Alembic migrations.

## Authentication and partitions

Every `/api/v1` request requires a bearer API key. Configure `ENSEMBLE_API_KEYS` as a JSON object keyed by the secret API key. Each entry declares a subject, allowed partitions, and permissions. Example (replace the placeholder secret):

```bash
export ENSEMBLE_API_KEYS='{"replace-with-a-long-random-secret":{"subject":"local-client","partitions":["default"],"permissions":["read","write"]}}'
```

Send `Authorization: Bearer <key>` and `X-Ensemble-Partition: <partition>` with API requests. If a key is limited to exactly one partition, the partition header may be omitted. Health and readiness endpoints are public; all versioned API and capability routes require authentication. Keys are never stored in the database. The configured partition is enforced in document identity, ingestion, lexical retrieval, and vector retrieval.

## Run locally

Python 3.12 or newer is required.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
alembic upgrade head
uvicorn ensemble.api:app --host 127.0.0.1 --port 8090
```

The default database is .ensemble/ensemble.db. Override it with ENSEMBLE_DATABASE_URL, for example:

```bash
ENSEMBLE_DATABASE_URL='postgresql+asyncpg://ensemble:secret@localhost/ensemble' alembic upgrade head
ENSEMBLE_DATABASE_URL='postgresql+asyncpg://ensemble:secret@localhost/ensemble' uvicorn ensemble.api:app --host 127.0.0.1 --port 8090
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
