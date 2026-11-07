### 7. Runtime resilience

- Define absolute request deadlines and cancellation propagation.
- Bound retries and prevent retry amplification.
- Avoid nested REST dispatch starvation within the single application.
- Define explicit partial-result, empty-result, unavailable, and not-ready semantics.
- Add durable operation tracking for long-running ingestion and rebuild jobs.
- Provide recovery and reconciliation for interrupted operations.

**Durable-operation audit (2026-11-07):** the remaining ingestion/rebuild paths were classified before adding another queue abstraction. Crawl and recurring-source work already use durable SQLite jobs with worker leases, heartbeats, retry, and expired-lease recovery. Transcript ingestion already persists byte checkpoints and job state; automatic recovery remains intentionally disabled until request-owned jobs have renewable leases, so one replica cannot interrupt another. Index generation rebuilds are durable operations through their generation records, bounded per-capability checkpoints, resumable cursors, explicit status, durable abort, and startup write-fence recovery (see section 5). The other document, PDF, URL, and Knowledge ingestion routes are request-scoped operations rather than detached background jobs. No additional long-running ingestion worker exists in this branch that needs a second job table. Any future adapter that detaches ingestion from its request must provide durable ownership, bounded status, retry/cancellation, and restart recovery before it is enabled.
