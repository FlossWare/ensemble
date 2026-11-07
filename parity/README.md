# POC parity tooling

This package measures the behavior and dependency surface of Ensemble against
`FlossWare/poc-claude-ensemble`. The POC is the reference implementation;
Ensemble is the candidate. A mapped endpoint or implemented class is not proof
of parity until the comparison has actually run.

## Components

- `scenarios/core-contracts.json`: versioned HTTP requests and comparison policy.
- `runner.py`: runs both systems and emits per-scenario observations and
  `PASS`, `FAIL`, `NOT_RUN`, or `ERROR`.
- `capability-map.json`: source-audited route and transport mapping for the
  tracked POC capability areas. Unmeasured capabilities remain explicit.
- `dependency-parity.json` and `dependency_audit.py`: check retained direct
  Python and Node dependencies and approved exclusions.
- `retrieval_metrics.py`: compare ranked results against a shared judged corpus
  using Recall@k, MRR@k, nDCG@k, quality deltas, Jaccard overlap, and Kendall tau.
- `corpora/`: corpus schema and a synthetic CI smoke fixture. The synthetic
  fixture is not a product-quality baseline.

## Run

From the repository root, configure the POC service base URLs documented in
[the harness guide](../docs/parity-harness.md), then run:

```sh
python -m parity.runner parity/scenarios/core-contracts.json --report parity-report.json
python -m parity.dependency_audit
python -m parity.retrieval_metrics parity/corpora/my-corpus.json --report retrieval-parity-report.json
```

Use `--strict` with the HTTP runner when a missing service should fail the run.
Do not turn unavailable reference services into a pass. Keep generated reports
and sensitive or production-derived query data out of source control unless
explicitly approved.

## Acceptance discipline

Exact JSON comparison is the default. Contract projections, ignored fields,
and numeric tolerances must be explicit in each scenario. Retrieval thresholds
must be based on a reviewed corpus, grading rubric, and acceptable regression
policy. Dependency substitutions require documented technical justification
and explicit approval, even when endpoint outputs appear equivalent.
