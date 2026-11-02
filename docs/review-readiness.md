# Code review readiness

**Review PR:** https://github.com/FlossWare/ensemble/pull/4  
**Review branch:** review/core-services-2026-11-02  
**Base:** main  
**Status:** Ready for code review. Not approved for merge.

## Review scope

This PR consolidates the current Ensemble implementation into one snapshot commit based on main. The previous parity branch carried unrelated historical ancestry; the snapshot preserves the implementation tree while avoiding hundreds of unrelated commits in the review history.

The implementation is one Python application and one REST server. SQLite is the only persistence implementation in this repository. REST is canonical; MCP and client integrations remain adapters.

## Implemented core workflows

- Document, lexical, transcript-session, PDF, URL, and Knowledge ingestion pass through Index revision decisions.
- Knowledge publication writes the durable document/chunk index and Knowledge provenance record, with optional vector indexing.
- Source revisions receive Graph source nodes and exact entity mention relationships.
- Hybrid retrieval combines lexical and embedding candidates, expands through Graph-linked sources, and can rerank the combined candidate set.
- Tombstones remove sources from active document retrieval and prune Knowledge vector records.
- Memory supports durable entries, source imports, reconciliation, lexical/semantic/hybrid search, and execution-context recall.
- Context assembly combines retrieval and Memory sources, preserves provenance, enforces an approximate character-based token budget, and uses extractive compression rather than generated summaries.
- Control Center exposes capability, retrieval, Context Assembly, Index/Vector, Memory/Knowledge, Graph, Entity, and pipeline diagnostic workbenches.

## Verification

The review branch runs GitHub Actions on every push and pull request. Latest verified result before the final UI update: 150 passed, 1 skipped. A fresh run on the final PR head must pass before merge. The suite includes API, persistence, migration, ingestion, Graph, Memory, Thompson, GA, Learning, and Control Center checks.

Do not use test count as a substitute for reviewing lifecycle and failure semantics.

## Explicitly incomplete

- Authentication, authorization, and partition isolation.
- Durable transcript/background ingestion jobs and restart-safe checkpoints.
- Safe index-generation rebuild and atomic cutover.
- Request deadlines, cancellation propagation, and complete recovery orchestration.
- Production metrics, tracing, rate limiting, backup/restore, and deployment/upgrade hardening.
- Multi-page crawling, robots policy, DNS-to-connection binding, scheduled refresh, and OCR.
- Approximate-nearest-neighbor indexing. Vector search currently performs exact SQLite scans.
- Exact model-tokenizer budgeting and model-backed context summarization. Current context budgets are estimates based on characters divided by four; compression is extractive.
- Model-backed intent classification and fuzzy entity resolution.
- External ranking-service fallback and full independent channel diagnostics.

## Review checklist

- [ ] Confirm REST contracts and OpenAPI match implementation.
- [ ] Review source revision, retry, tombstone, and stale-write behavior.
- [ ] Review Graph entity identity and source relationship semantics.
- [ ] Review partial dependency failure behavior in Hybrid and Context.
- [ ] Review SQLite migrations and restart behavior.
- [ ] Review security and scope limitations against intended deployment.
- [ ] Verify latest GitHub Actions run is green on the PR head.
- [ ] Resolve review comments and update implementation status.
- [x] Convert from draft after the latest code and UI changes have passed CI.

## Merge policy

This PR is for code review only. It must not be merged without explicit approval after review.
