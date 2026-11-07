# Generic Contract Audit

**Scope:** Python implementation currently present in this repository. REST contracts remain language-neutral.

## Implemented generic contracts

| Contract | Type-safety decision |
|---|---|
| `DeletionStrategy[TContext, TValue]` | Generic context and result value preserve the wrapped strategy's input/output types. |
| `DeletionResult[TValue]` | Generic result payload avoids erasing the storage-specific reclamation result. |
| `DeletionStrategyDecorator[TContext, TValue]` | Generic decorator base preserves both types through composition. |
| `RetryingDeletionDecorator[TContext, TValue]` | Preserves the inner strategy's context/result contract. |
| `AuditingDeletionDecorator[TContext, TValue]` | Audit callback receives the same context and typed result as the wrapped strategy. |
| `RetentionDeletionDecorator[TValue]` | Constrains retention to the deletion context while preserving the wrapped result type. |
| `ReclamationQueue` / `ReclamationJob` | Protocol and TypedDict expose the durable queue contract and its metadata without untyped result dictionaries. |

## Foundational contracts reviewed

The Document, Index, Knowledge, Vector, Graph, Memory, and Collaboration store protocols were reviewed.

These contracts describe concrete Ensemble resources and their domain operations. Their current method inputs/outputs are already concrete domain values or explicitly structured JSON payloads. Adding arbitrary type parameters to each store would not enable meaningful substitution: a document store still stores Ensemble Documents, an index store still manages source/generation records, and a vector store still manages vector records. Genericizing those interfaces would add type-parameter noise without improving runtime behavior or compatibility.

Where a future adapter genuinely needs a different domain value shape, introduce a bounded protocol or type parameter at that abstraction boundary rather than turning every storage protocol into a generic repository.

## Java implementation boundary

There are currently no Java source files or Java build module in this repository. The implementation available to audit and test here is Python. A Java mirror would be a fabricated parallel implementation, not parity work against an existing Java codebase, and would create a second implementation surface with no current runtime or tests.

The Python contracts are therefore implemented and audited here. If a Java implementation is introduced in this repository or a designated companion repository, its interfaces should mirror the language-neutral lifecycle semantics while using Java generics locally. REST request/response shapes must not expose Python or Java generic types.

## Validation

- Strategy/decorator behavior and composition are covered in `tests/test_deletion.py`.
- Deletion queue persistence and restart behavior are covered in `tests/test_reclamation.py`.
- The full repository test suite is the behavioral compatibility gate.
