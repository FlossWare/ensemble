# Generation consistency and recovery

## Status

This document defines the safety contract for rebuilding the active retrieval generation. It is an implementation contract, not a claim that generation rebuild is currently available. The rebuild operation must remain unavailable until every required phase and recovery path is implemented and tested.

The initial implementation targets the existing single-application, SQLite deployment. It does not require a broker, distributed lock service, service mesh, or separate worker fleet.

## Required invariants

1. **One authoritative active generation.** Reads resolve through one active-generation pointer. A candidate generation is never visible to normal retrieval before cutover.
2. **A consistent starting point.** The builder records a durable source snapshot boundary. All source state and derived data copied for that build must be attributable to that boundary or to a later acknowledged operation replayed into the candidate.
3. **Acknowledged writes are never lost.** An ingestion or deletion operation is acknowledged only after its authoritative source decision and all required active-generation writes have completed, or after a durable operation record exists that guarantees completion/replay.
4. **Deletes win.** A tombstone accepted after the snapshot boundary must be replayed into the candidate. A stale revision must not resurrect a tombstoned source.
5. **Candidate isolation.** Candidate writes and validation must not change active-generation query results.
6. **Verified cutover.** The active pointer changes only after all required stores are caught up to the same operation watermark and candidate verification succeeds.
7. **Crash resumability.** Every phase transition, cursor, snapshot boundary, operation watermark, and verification result needed to resume or abort is durable.
8. **Safe cleanup.** The prior active generation is retained until the new generation is active and has passed post-cutover health checks. Failed or abandoned candidates can be deleted only after confirming they are not active.

## State machine

A generation has one of these states:

- `building`: snapshot and bounded copy passes are in progress.
- `reconciling`: candidate is replaying operations accepted after the snapshot boundary.
- `fenced`: new writes are briefly prevented from crossing the cutover boundary; already admitted writes must finish or be replayed.
- `verifying`: all stores are checked against the final operation watermark and candidate invariants.
- `ready`: candidate is verified and eligible for activation.
- `active`: candidate is the sole active generation.
- `failed`: build cannot safely continue; active generation remains unchanged.
- `aborted`: operator cancelled the build; active generation remains unchanged.

Only the following forward transitions are valid:

`building -> reconciling -> fenced -> verifying -> ready -> active`

A failure or explicit cancellation may transition any non-active state to `failed` or `aborted`. A failed/aborted candidate is never promoted implicitly. Recovery resumes from the last durable checkpoint when its preconditions still hold; otherwise it fails closed and leaves the active generation untouched.

## Snapshot and write protocol

The snapshot boundary and operation journal must be coordinated with authoritative source decisions. A timestamp or maximum source ID is not a consistent snapshot.

The protocol must provide:

- A monotonically ordered operation sequence allocated transactionally with the authoritative Index decision.
- A stable snapshot watermark captured while writes are coordinated.
- Durable operation identity and payload sufficient to replay accepted revisions and tombstones idempotently.
- Per-store applied watermarks or equivalent proof that each derived store has applied every operation through a given sequence.
- A short cutover fence that prevents a write from being acknowledged on only one side of the final watermark.
- A final reconciliation pass through the fence watermark before verification.

The operation journal is not merely an audit log. Its retention and acknowledgement rules must guarantee that an operation cannot be pruned while any candidate that may need it remains recoverable.

## Copy and reconciliation order

The candidate must include every retrieval-relevant representation, not only vectors:

1. Index source state and revision/tombstone metadata.
2. Documents and chunks, including chunk provenance and source revision.
3. Lexical retrieval structures derived from those documents.
4. Vector collections, including collection model identity, dimensions, record identity, and source provenance.
5. Knowledge records and their links to source/document revisions.
6. Any other retrieval-visible derived state that is part of the active generation contract.

Each pass is bounded and checkpointed. Checkpoints include the snapshot watermark, cursor, upper bound where applicable, copied/skipped counts, and a checksum or verification summary. A checkpoint is sealed only after its pass has completed against its captured boundary.

After the initial copy, replay the durable operation journal in sequence. Revisions, updates, and tombstones must be idempotent. A tombstone must remove or exclude every derived representation, including all vector collections and lexical/document retrieval state.

## Verification and atomic cutover

Before cutover:

- Every required pass is sealed.
- The candidate has applied all operations through the final fence watermark.
- Every acknowledged operation through that watermark is represented in the candidate or is an accepted tombstone.
- No candidate record references a missing or superseded source revision.
- Vector collection model/dimension metadata matches the captured collection definition.
- Candidate counts and deterministic checksums are recorded for the verification scope.
- The active pointer still identifies the generation from which the candidate was built.

Activation is one atomic metadata transaction that verifies the expected previous active generation and switches the pointer to the candidate. If the expected previous generation changed, activation fails and requires reconciliation/revalidation. There must be no sequence of independent per-store pointer changes.

## Restart recovery

On application startup, inspect nonterminal generations:

- Resume `building` and `reconciling` only from valid durable checkpoints and retained journal ranges.
- Re-enter the cutover fence only after confirming no prior fence owner remains.
- Re-run verification if the recorded verification watermark is behind the current journal watermark.
- If state is `ready`, verify the active pointer and expected prior generation before activation.
- If state is `active`, confirm the active pointer agrees with the generation record.
- Never infer success from a partially completed store operation or a stale in-memory flag.

Recovery must be idempotent and safe to run more than once.

## Required failure tests

The implementation is not complete without tests that inject failure or process restart:

- During each bounded copy pass, before and after checkpoint persistence.
- After an authoritative write decision but before each derived-store write.
- After a derived-store write but before the operation is marked applied.
- During journal replay, including duplicate replay.
- During tombstone replay and concurrent stale revision attempts.
- While acquiring and releasing the cutover fence.
- Immediately before and after atomic active-pointer switch.
- During old-generation retention and cleanup.

Assertions must prove that the old active generation remains queryable until successful cutover, every acknowledged write survives, deleted sources never reappear, and recovery converges without duplicate visible records.

## Scope constraints

The initial implementation remains within the existing Ensemble application and SQLite persistence model. Capability work continues through the REST contracts established by the application. Do not introduce distributed coordination, federation, a broker, or a separate worker service unless a demonstrated deployment requirement makes the single-process SQLite protocol insufficient.
