# Ensemble Architecture

**Status:** Initial REST-first vertical slice implemented. Chunking, embeddings, and indexing are independent REST capability contracts. Authentication, partition enforcement, a dedicated vector index, and production deployment hardening remain future work.

## Purpose and scope

Ensemble is the standalone platform for reusable intelligence capabilities: memory, knowledge, retrieval, embeddings, ranking, reranking, and related operations. Client integrations belong in separate consumer repositories. Historical claude-ensemble and poc-claude-ensemble repositories are reference material, not runtime dependencies.

Ensemble operates without Claude Code, Codex, Cursor, Crush, MCP, or any other AI client.

## Deployment shape

One application, one REST listener, and multiple versioned endpoint groups. Capabilities may share a process initially but retain independent REST contracts and ownership so they can move to another process or host without changing consumers. Do not introduce federation, service discovery, service meshes, brokers, or a microservice fleet without a concrete requirement.

## REST is the canonical boundary

REST/HTTP is canonical for external consumers and internal capability-to-capability communication. A capability calls another capability through its REST contract even when co-located. The initial deployment uses ASGI-backed HTTP transport, which exercises HTTP routing, request validation, and JSON serialization without requiring a network listener. A future deployment can change the HTTP origin without changing the contract.

Implemented capability endpoints:
- `POST /api/v1/capabilities/chunking/split`
- `POST /api/v1/capabilities/embeddings`
- `POST /api/v1/capabilities/index/documents`
- `GET /api/v1/capabilities/index/chunks`

Public ingestion and search orchestrate these contracts. Capability implementations do not directly invoke peer implementation objects. Contracts are language-neutral.

## Composition and security

Decorators may wrap capability REST clients while preserving contracts. Expected concerns include logging, metrics, tracing, timeouts, bounded retries, caching, provider routing, policy, authorization, and partition-aware access enforcement.

Decorators are not the sole security boundary. Authorization must also be enforced at storage/query boundaries. The current implementation requires bearer API keys configured through `ENSEMBLE_API_KEYS`, checks read/write permissions, and enforces allowed partition membership on every versioned API and capability route. Partition scope is stored with document identity and applied to ingestion, lexical candidate retrieval, and vector candidate retrieval. Health/readiness remain public. Key rotation is performed by updating the deployment environment and restarting the service; secrets are not stored in the database.

## Retrieval

The current search path asks the index capability for a bounded candidate set using database-side lexical matching, then combines lexical ranking, optional cosine similarity over those candidates, and reciprocal-rank fusion. Embeddings are retained as portable JSON and also written to a native pgvector column on PostgreSQL. PostgreSQL uses an HNSW cosine index for independent vector candidate retrieval; SQLite uses an exhaustive local fallback. Lexical and vector candidates are independently bounded and fused. PostgreSQL deployments must set `ENSEMBLE_EMBEDDING_DIMENSIONS` to match the configured embedding model before applying migrations. Reranking, graph expansion, and authorization-aware candidate filtering are not yet implemented. Generation remains optional and provider-backed.

## Storage and migration

Ensemble owns its persistence and data access. It must not depend on POC database files, persistence directories, process internals, or direct database sharing.

SQLAlchemy provides portable relational tables. SQLite is the local default; PostgreSQL is supported and intended for shared deployment. Alembic migrations are applied as a deployment step. Startup checks the migration table and required Ensemble tables but does not create or silently upgrade production schemas.

Existing POC services and data remain untouched. Do not stop ingestion, relocate storage, re-ingest documents, regenerate embeddings, or run destructive experiments against the POC. Any migration uses a supported API or explicit export/import contract.

## Scope and sequence

Complete: repository conventions, REST contract standards, application skeleton, first vertical slice through Ensemble-owned REST capability contracts and persistence.

Next: bounded lexical/vector retrieval and a real vector index; then reranking, memory, graph, ingestion connectors, evaluation, authentication, and partition enforcement. Do not expose Ensemble as a multi-user shared service until authorization and partition controls are implemented and tested.

## Architectural acceptance

The first vertical slice operates without an AI client, MCP, or generation provider. Co-located capability calls cross HTTP routes and JSON serialization. A capability can move behind another HTTP origin without changing its request/response contract.
