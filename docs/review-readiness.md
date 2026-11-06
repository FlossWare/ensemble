# Code review readiness

**Implementation PR:** https://github.com/FlossWare/ensemble/pull/47  
**Implementation branch:** integration/source-lifecycle-mainline-2026-11-04  
**Base:** main  
**Status:** Implementation in progress. Not ready for outside review or merge.

## Current verified state

- The implementation PR contains the source lifecycle, retrieval/operational diagnostics, and collaboration work.
- The repository currently has zero open issues; #26 was explicitly closed as not planned because this repository has no Java source/build target, and #27 and #43–#46 were closed as completed.
- The current implementation PR is the only open PR.
- The latest verified Python workflow on head `29362779405a0890fc5d274bbadf77813522fd3c` passed: 345 passed, 1 skipped, 0 failed. Installer shell syntax, Control Center JavaScript syntax, and GitGuardian checks also passed on the recorded validation run.
- The suite's one skip and any warnings must remain visible and have an explained disposition; do not weaken tests to obtain a green run.

## Remaining implementation gaps

- Internal capability-to-capability orchestration still has direct store/service calls. The accepted architecture requires internal REST clients to use the same REST contracts as external consumers, including contract-level tests.
- Continue validating lifecycle correctness across ingestion, retrieval, tombstones, reclamation, restoration, purge, and generation cutover.
- Continue Memory source reconciliation, event lifecycle, conflict/deletion policy, and retrieval-quality verification.
- Complete bounded deadlines/cancellation and durable operation tracking for long-running work.
- Finish Control Center diagnostics and operational visibility for all capabilities, with all operations REST-backed.
- Complete operational readiness: backup/restore, migration/upgrade, observability, rate limits, authentication/authorization, and partition enforcement.
- Verify the embedding/reranking runtime matrix and retain exact vector scans unless measured workload justifies an ANN implementation.
- Decide OCR only with a deliberate provider/runtime decision; provide source connector contracts without vendor coupling.

## Validation requirements

- Unit tests for domain transitions, validation, deterministic algorithms, decorators, errors, and boundary conditions.
- Persistence/integration tests for migrations, transaction boundaries, restart durability, retries, partition isolation, and cross-store consistency.
- REST integration tests for request/response contracts, authorization, partition scoping, invalid transitions, errors, and Control Center workflows.
- Concurrency tests for competing updates, duplicate dispatch, leases/heartbeats, and lifecycle races.
- Failure-injection tests for partial writes, interruption, unavailable dependencies, rollback, retry, and recovery.
- End-to-end tests for ingestion, retrieval, deletion/reclamation, rebuild/cutover, and collaboration.
- Java/Python parity only where both implementations exist. Ensemble is Python-only today; do not invent a Java runtime solely to satisfy a parity checklist.

## Review and merge policy

- No outside reviewers during implementation.
- Do not merge PR #47 yet.
- Complete feature parity, resolve all issues, ensure required unit/integration/end-to-end tests pass, and consolidate the PR queue first.
- Only then run SonarCloud/SonarQube and require its quality gate to pass.
- Request outside review after those gates; resolve findings before final merge.
