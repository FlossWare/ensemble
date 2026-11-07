# POC vs Ensemble Parity Harness

The parity harness makes observable POC behavior comparable with Ensemble behavior without coupling either application to the harness or adding another Ensemble service.

## Design rules

- The POC is the behavioral reference; Ensemble is the candidate.
- Both systems are exercised through HTTP contracts. Do not compare private implementation details as a substitute for caller-visible behavior.
- Every scenario is versioned and names its capability, method, path, request, and comparison policy.
- Exact JSON equality is the default. Differences may be ignored or assigned numeric tolerances only when the scenario documents why.
- HTTP status and response body are both compared.
- Missing base URLs or unavailable reference services are **NOT_RUN**, never PASS. Transport failures are **ERROR**. A real behavioral mismatch is **FAIL**.
- Dependency parity is reported independently. A behavioral PASS does not approve a dependency substitution.
- POC behaviors that do not exist in the POC are not invented as acceptance requirements. Intentional architectural adaptations must be documented.

## First scenario set

`parity/scenarios/core-contracts.json` begins with health contracts for Chunking and Tokenization. These are deliberately small smoke checks, not a claim of functional parity. Expand each capability with behavior scenarios only after confirming its request and response contracts from both source trees.

## Run

Set the POC and Ensemble service base URLs for the scenario set. The initial POC examples assume the POC Chunking or Tokenization service is the selected reference base URL; because those POC services run independently, run one capability group per matching service endpoint or extend the scenario with a per-capability environment variable.

```sh
POC_BASE_URL=http://127.0.0.1:8775 \
ENSEMBLE_BASE_URL=http://127.0.0.1:8080 \
python -m parity.runner parity/scenarios/core-contracts.json \
  --report parity-report.json
```

Use `--strict` when a run must fail if any scenario could not execute. The report includes per-scenario observations, differences, summary counts, and an overall state. Do not check generated reports containing machine-specific URLs or runtime details into source control.

## Comparison policy

- JSON objects compare recursively and ignore object key ordering.
- Arrays compare exactly and in order by default. This is intentional for ranked results and ordered event histories.
- `ignore_json_pointers` is for known volatile fields only.
- `numeric_tolerances` maps JSON pointers to absolute numeric tolerances. It must not be used to paper over ranking, confidence, or model drift without an explicit quality rationale.
- `ordered_json_pointers` documents arrays whose ordering is contractually meaningful; array ordering is already strict by default.
- Add retrieval-specific metrics (Recall@k, MRR, nDCG, rank agreement) as separate evaluators over a shared judged corpus. Do not reduce retrieval quality to raw JSON equality.

## Current limitations

This first increment provides the manifest-driven HTTP runner, deterministic comparison policy, machine-readable report, and unit tests. It does not yet include all POC-to-Ensemble route mappings, a shared judged retrieval corpus, live model/runtime comparisons, or a Control Center view. Those require source-audited scenario contracts and reference/candidate environment wiring. A capability with no executed scenario remains unmeasured, not complete.
