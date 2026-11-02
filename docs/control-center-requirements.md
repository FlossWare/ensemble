# Control Center Requirements

**Status:** Accepted requirements; implementation is incremental.
**Scope:** Ensemble's browser-based operational and diagnostic interface, served by the Ensemble application and backed exclusively by Ensemble REST contracts.

## 1. Purpose

The Control Center is the human-facing administration, observability, and investigation surface for Ensemble. It must make the platform's capabilities understandable and inspectable without requiring direct database access, shell access, or knowledge of internal Python modules.

The Control Center is not a second implementation of Ensemble logic. It is a client of the same versioned REST contracts used by other consumers.

## 2. Accepted design decisions

1. **Read-only first.** Initial workbenches should expose health, configuration, state, statistics, and inspection before adding mutation or maintenance workflows.
2. **REST-only browser boundary.** Browser code communicates with the Ensemble REST API. It must not connect directly to SQLite, read database files, or bypass capability contracts.
3. **Capability-owned diagnostics.** Each capability owns the health, status, statistics, and diagnostic information it exposes. The Control Center composes those REST responses.
4. **Consistent observability.** Where meaningful, capabilities should expose a consistent set of health/readiness, configuration summary, statistics, recent activity, and diagnostic information.
5. **Explicit operations.** Mutating or potentially destructive actions (rebuild, reset, delete, re-ingest, configuration changes, tombstoning, and similar operations) require explicit user initiation, clear impact descriptions, and confirmation. Never hide mutations behind a page load or inspection action.
6. **No implied live configuration changes.** Experimental or optimized candidates are proposals until an explicit, separately defined apply operation exists. Exporting or approving a candidate must not silently change active runtime configuration.
7. **No POC coupling.** The Control Center must not inspect, modify, or depend on POC process internals, databases, or persistence directories.

## 3. Information architecture

The Control Center should organize workbenches by platform concern. Navigation labels may evolve, but the following capability coverage is required.

### 3.1 Platform overview

- Overall service health and readiness.
- Capability status and endpoint availability.
- Runtime version and deployment information where exposed.
- Persistence status, database location, schema/migration version, and database size where safely available through REST.
- Configured providers and model availability, without exposing secrets.
- Recent errors and operational events where available.

### 3.2 Intelligence and decision systems

**Thompson sampling**
- Model/arm inventory and eligibility.
- Alpha and beta parameters, derived posterior summaries, and observed successes/failures.
- Selection history and outcome history.
- Cost constraints and why a candidate was or was not eligible.
- Reset operations only as explicit, confirmed actions.
- Clearly distinguish model-level routing from task/capability-aware routing; do not imply filtering that the service does not implement.

**Genetic algorithm (GA)**
- Evaluator and parameter-set inventory.
- Run configuration, seed, status, generation progress, and fitness history.
- Candidate comparison and parameter bounds.
- Approval/export history.
- Clearly distinguish synthetic/local evaluators from production workload measurements.
- No automatic application of candidate settings.

**Learning**
- Recent outcomes and validation status.
- Checkpoint and state summaries.
- Reports and portable artifacts.
- Links between recorded outcomes and any Thompson updates or Memory events.
- Reset and other state-changing operations behind explicit confirmation.

### 3.3 Data stores and knowledge structures

**Graph**
- Node and edge counts, types, and relationship distributions.
- Recent mutations and graph status.
- Bounded neighborhood inspection and directional traversal.
- Clear indication of traversal limits and filters.
- No unbounded graph dumps by default.

**Vector**
- Collection inventory, dimensions, embedding identity, and record counts.
- Collection health and storage statistics where available.
- Record inspection with source/document provenance.
- Similarity-query workbench showing query parameters, scores, and returned records.
- Explicit warnings that exact scan is not an approximate-nearest-neighbor index.

**Lexical**
- FTS/index status, indexed document counts, and tokenizer/analyzer identity.
- Query workbench showing matches and BM25 scores.
- Tokenization details sufficient to explain identifier and compound-term behavior.

**Knowledge**
- Source and record inventory, provenance, revision, publication/ingestion status, and freshness.
- Lookup by source, identifier, or record.
- Clear relationship to Index revisions and source documents.

**Memory**
- Memory/session/event inventory and counts.
- Scoped search and recall inspection.
- Event provenance, idempotency identifiers, and source metadata where exposed.
- Retention and cleanup information where implemented; no assumed retention policy.

### 3.4 Processing capabilities

**Chunking**
- Available profiles and profile versions.
- Chunk size, overlap, boundaries, and profile-specific settings where applicable.
- Preview of chunk boundaries and metadata for supplied text/document content.
- Persisted chunk provenance and the profile used during ingestion.
- Clear distinction between preview behavior and persisted ingestion behavior.

**Tokenization**
- Analyzer/profile inventory and version.
- Normalization and identifier handling.
- Token preview with offsets and relevant metadata.

**Embeddings**
- Provider/model identity, dimensions, configured backend/device, and readiness.
- Safe operational metrics such as request counts, latency, and failures when available.
- Clear indication when embeddings are unavailable or degraded.
- Never expose credentials or secret provider configuration.

**Reranking**
- Provider/model identity, readiness, and configured limits.
- Candidate list before and after reranking, with scores and stable identity alignment.
- Explain fallback/degraded behavior and whether reranking actually ran.

**Indexing**
- Source manifests, active revision, revision history, and tombstones.
- Reconciliation and stale-revision decisions.
- Rebuild/reindex operations only as explicit, confirmed actions with bounded scope.

**Extraction and Intent**
- Extracted identifiers/entities, confidence, and uncertainty.
- Intent labels, evidence, and per-label scores.
- Distinguish rule-based behavior from any future model-backed provider.

### 3.5 Retrieval workbench

Provide an interactive way to investigate retrieval without requiring a generation model.

- Run Lexical, Vector, and Hybrid retrieval independently where supported.
- Compare channel results for the same query.
- Display fusion contributions, rank positions, and final ordering.
- Show reranking effects separately from initial retrieval and fusion.
- Expose degraded dependencies and partial-result behavior.
- Preserve source, document, chunk, revision, and other provenance through each stage.
- Make query settings and filters visible and reproducible.
- Do not imply that an unsupported channel or pipeline stage ran.

### 3.6 Administration and diagnostics

Over time, provide:
- Runtime configuration inspection and safe configuration workflows.
- Database and collection maintenance.
- Ingestion, rebuild, and reindex job inspection.
- Diagnostics and recent operational events.
- Experiment and evaluation inspection.
- Backup and recovery status or guidance.

Administrative actions must use explicit REST operations, be scoped, auditable where supported, and guarded by confirmation when they can alter or destroy state. Credentials and secrets must be redacted.

## 4. Pipeline inspector

The Pipeline Inspector is an explicit first-class workbench, not merely a debug log or a collection of unrelated forms. It explains how a document or query moves through the capabilities and what each stage contributes.

### 4.1 Document/ingestion path

For a supplied document or supported source, inspect applicable stages:

1. Source identification and validation.
2. Extraction/content normalization.
3. Tokenization and analyzer output.
4. Chunking profile selection and chunk boundaries.
5. Embedding provider/model and vector dimensions, when enabled.
6. Index revision decision and persistence outcome.
7. Lexical and Vector writes.
8. Knowledge publication/provenance, when applicable.

Display each stage's input/output summary, status, duration where measured, warnings/errors, and stable identifiers linking outputs to later stages. Provide a clear preview-only mode. Persisting or re-ingesting content must be a separate explicit action.

### 4.2 Query/retrieval path

For a query, inspect applicable stages:

1. Query validation and normalization.
2. Tokenization and intent classification.
3. Lexical retrieval.
4. Query embedding and Vector retrieval.
5. Optional Graph expansion or related-session lookup.
6. Candidate filtering and reciprocal-rank fusion.
7. Optional reranking.
8. Final result ordering and provenance.

For each stage, show whether it ran, its parameters, candidate counts, relevant scores/rank contributions, duration where measured, and any degraded/fallback behavior. Make it possible to compare intermediate and final candidate sets without changing persisted state.

### 4.3 Pipeline trace and correctness

- Each inspection should have a trace/run identifier and a timestamp.
- Stage output references must be stable enough to correlate records across the pipeline.
- The inspector must distinguish actual execution data from estimates, previews, and explanatory metadata.
- A skipped stage must be labeled skipped with a reason; it must not be rendered as a successful execution.
- Errors should identify the failing capability and preserve useful upstream results when the underlying API supports degraded operation.
- Avoid retaining document contents or sensitive query payloads beyond existing platform persistence and explicit user actions. Any future trace retention needs a defined policy.
- Pipeline inspection must use public/internal REST contracts, not direct function calls or database reads from browser code.

## 5. Interaction and safety model

- Default views are read-only.
- Preview and inspect operations must not mutate persistent state.
- Mutations are visibly separated from inspection.
- Destructive actions require a confirmation that names the target and consequence.
- Long-running operations, when introduced, need visible status, progress, failure details, and retry semantics.
- Empty, unavailable, degraded, and unsupported states must be distinct.
- UI claims must reflect implemented backend behavior. Do not display placeholder metrics as live measurements.

## 6. API and implementation requirements

- The Control Center is served by the Ensemble application and calls versioned REST endpoints.
- Capability APIs own their diagnostics and operational semantics.
- Add endpoints only where there is a concrete UI or consumer requirement; avoid UI-specific duplication of business logic.
- Responses should be bounded and paginated where collections can grow.
- Metrics should identify units, time windows, and whether values are cumulative, sampled, or instantaneous.
- API errors and partial failures should be represented consistently and surfaced without discarding usable results.
- Browser code must not depend on private SQLite schemas or implementation modules.
- The interface must remain useful when optional providers are unavailable.

## 7. Delivery sequence

1. **Inventory and contract mapping:** map every workbench and pipeline stage to existing REST endpoints; mark gaps explicitly.
2. **Read-only foundation:** platform overview, capability status, and diagnostics using existing contracts.
3. **Capability workbenches:** Thompson, Graph, Vector, Lexical, Chunking, Tokenization, Embeddings, Reranking, Index, Knowledge, Memory, GA, and Learning.
4. **Retrieval comparison:** channel-by-channel retrieval, fusion, reranking, and provenance.
5. **Pipeline Inspector:** document/ingestion and query/retrieval traces, beginning with stages whose contracts already expose the necessary data.
6. **Controlled administration:** add scoped maintenance and configuration actions only after the read-only and confirmation model is established.
7. **Validation:** contract tests, browser tests, accessibility checks, and operational tests for unavailable/degraded dependencies.

This is an incremental delivery order, not permission to claim incomplete work as implemented.

## 8. Acceptance criteria

The Control Center is acceptable when:

- Every listed capability has either a useful workbench or an explicit, visible not-yet-implemented status.
- The browser uses REST contracts exclusively.
- Read-only inspection cannot mutate persistent state.
- Mutations are explicit, scoped, and confirmed according to impact.
- Thompson, Graph, Vector, Chunking, and the other named subsystems expose understandable state and behavior rather than merely a generic health badge.
- Retrieval results can be compared across supported stages with provenance and honest degraded-state reporting.
- The Pipeline Inspector can show the actual stages executed for both ingestion and retrieval, distinguish skipped/unavailable stages, and correlate intermediate outputs to final results.
- No UI presents synthetic, placeholder, or inferred values as live operational measurements.
- Existing POC services and data remain untouched.
