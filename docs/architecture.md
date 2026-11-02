# Ensemble Architecture

**Status:** REST-first platform foundation implemented. The current implementation includes authenticated, partition-scoped document ingestion/retrieval, PostgreSQL vector indexing, memory, graph, tokenization, and provider-backed reranking contracts. Production hardening and several advanced capabilities remain open.

## Purpose and scope

Ensemble is the standalone platform for reusable intelligence capabilities: memory, knowledge, retrieval, embeddings, ranking, reranking, graph operations, and related services. Client integrations belong in separate consumer repositories. Historical claude-ensemble and poc-claude-ensemble repositories are reference material, not runtime dependencies.

Ensemble operates without Claude Code, Codex, Cursor, Crush, MCP, or any other AI client.

## Deployment shape

One application, one REST listener, and multiple versioned endpoint groups. Capabilities may share a process initially but retain independent REST contracts and ownership so they can move to another process or host without changing consumers. Do not introduce federation, service discovery, service meshes, brokers, or a microservice fleet without a concrete requirement.

## REST is the canonical boundary

REST/HTTP is canonical for external consumers and internal capability-to-capability communication. A capability calls another capability through its REST contract even when co-located. The initial deployment uses ASGI-backed HTTP transport, which exercises HTTP routing, request validation, and JSON serialization without requiring a network listener. A future deployment can change the HTTP origin without changing the contract.

Implemented capability groups include tokenization, character-window chunking, embeddings, optional reranking, index persistence and candidate retrieval, memory, and graph. Public ingestion and search orchestrate capability contracts. Capability implementations do not directly invoke peer implementation objects. Contracts are language-neutral.

## Authentication and partition isolation

Every versioned API and capability endpoint requires a bearer API key configured through `ENSEMBLE_API_KEYS`. Each key declares a subject, permissions, and an allow-list of partitions. `X-Ensemble-Partition` selects the active partition; a single-partition key may omit the header. Health and readiness remain public.

Partition scope is enforced in persistence queries and document identity, not merely in the HTTP layer. Ingestion, lexical retrieval, vector retrieval, memory operations, graph nodes, graph edges, and graph traversal all apply the authenticated partition. Internal capability calls propagate the authenticated caller and partition. Secrets are not stored in the database.

## Retrieval

The public search path retrieves bounded lexical and vector candidate sets independently, then applies reciprocal-rank fusion. PostgreSQL stores embeddings in pgvector and uses an HNSW cosine index. Set `ENSEMBLE_EMBEDDING_DIMENSIONS` to match the embedding model before applying migrations. SQLite uses an exhaustive vector-search fallback for local development.

The current lexical retriever uses database-side text matching. Search candidate channels are bounded to at most 1,000 candidates each. The public search pipeline does not yet invoke the optional reranking capability. The tokenizer is deterministic Unicode word/punctuation tokenization, not a model-specific BPE tokenizer. Chunking remains character-window based.

## Memory and graph

Memory is persisted by partition and key, with category, importance, metadata, and optional expiry. Expired memories are excluded from reads and search. Graph nodes and edges are partition-scoped; edges can only connect nodes within the active partition. Neighborhood traversal is bounded to four hops.

These are useful baseline capabilities, not a substitute for a richer graph query language, semantic memory retrieval, or graph/vector hybrid retrieval.

## Composition and operations

Decorators may wrap capability REST clients while preserving contracts. Expected cross-cutting concerns include logging, metrics, tracing, timeouts, bounded retries, caching, provider routing, policy, authorization, and partition-aware access enforcement. Any future cache or reranker must preserve caller and partition scope.

## Storage and migration

Ensemble owns its persistence and data access. It must not depend on POC database files, persistence directories, process internals, or direct database sharing.

SQLAlchemy provides relational persistence. SQLite is the local default; PostgreSQL is intended for shared deployment and provides pgvector indexing. Alembic migrations are applied as a deployment step. Startup checks the migration version and does not create or silently upgrade production schemas.

Existing POC services and data remain untouched. Do not stop ingestion, relocate storage, re-ingest documents, regenerate embeddings, or run destructive experiments against the POC. Any future data migration uses a supported API or explicit export/import contract.

## Remaining work

- Integrate optional reranking into the public search pipeline.
- Add token-aware chunking and semantic memory retrieval.
- Add extraction, ingestion connectors, and background jobs.
- Add richer graph query constraints and graph/vector hybrid retrieval where justified.
- Add pagination, request budgets, timeouts, production metrics/tracing, audit events, key rotation automation, rate limits, and deployment packaging.
- Validate retrieval quality and performance with the independent Evaluation project.

## Architectural acceptance

Ensemble starts independently of both POC repositories. Capability-to-capability calls cross documented REST contracts, including when co-located. No Ensemble code reads or writes POC storage. Every knowledge operation preserves partition scope and provenance. A capability can move behind another HTTP origin without changing its request/response contract.
