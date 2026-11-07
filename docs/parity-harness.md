# POC vs Ensemble Parity Harness

The parity harness makes observable POC behavior comparable with Ensemble behavior without coupling either application to the harness or adding another Ensemble service.

## Design rules

- The POC is the behavioral reference; Ensemble is the candidate.
- Both systems are exercised through HTTP contracts. Do not compare private implementation details as a substitute for caller-visible behavior.
- Every scenario is versioned and names its capability, method, path, request, and comparison policy.
- Exact JSON equality is the default. Differences may be ignored or assigned numeric tolerances only when the scenario documents why.
- HTTP status and response body are both compared; a matching 404 is still a failure because each scenario expects HTTP 200 unless it explicitly overrides `expected_status`.
- Missing base URLs or unavailable reference services are **NOT_RUN**, never PASS. Transport failures are **ERROR**. A real behavioral mismatch is **FAIL**.
- Dependency parity is reported independently. A behavioral PASS does not approve a dependency substitution.
- POC behaviors that do not exist in the POC are not invented as acceptance requirements. Intentional architectural adaptations must be documented.

## First scenario set

`parity/scenarios/core-contracts.json` now contains health and behavior scenarios for Chunking and Tokenization, deterministic Reciprocal Rank Fusion, entity extraction, and intent classification. Extraction compares shared entity fields while ignoring Ensemble-only identity/provenance metadata; intent compares the common label and confidence contract. These are targeted contracts, not a claim that the entire capability is at parity.

## Run

Set the POC service base URLs independently because the POC runs these capabilities on separate listeners. Ensemble uses its single REST listener.

```sh
POC_CHUNKING_URL=http://127.0.0.1:8775 \
POC_TOKENIZATION_URL=http://127.0.0.1:8776 \
POC_RANKING_URL=http://127.0.0.1:8780 \
POC_EXTRACTION_URL=http://127.0.0.1:8778 \
POC_INTENT_URL=http://127.0.0.1:8779 \
POC_HYBRID_URL=http://127.0.0.1:8773 \
POC_KNOWLEDGE_URL=http://127.0.0.1:8781 \
POC_GRAPH_URL=http://127.0.0.1:8766 \
POC_EMBEDDING_URL=http://127.0.0.1:8770 \
POC_VECTOR_URL=http://127.0.0.1:8771 \
POC_LEXICAL_URL=http://127.0.0.1:8772 \
POC_RERANKING_URL=http://127.0.0.1:8774 \
POC_INDEX_URL=http://127.0.0.1:8777 \
ENSEMBLE_BASE_URL=http://127.0.0.1:8080 \
python -m parity.runner parity/scenarios/core-contracts.json \
  --report parity-report.json
```

Use `--strict` when a run must fail if any scenario could not execute. The report includes per-scenario observations, differences, summary counts, and an overall state. Do not check generated reports containing machine-specific URLs or runtime details into source control.

## Comparison policy

- JSON objects compare recursively and ignore object key ordering.
- Arrays compare exactly and in order by default. This is intentional for ranked results and ordered event histories.
- `ignore_json_pointers` is for known volatile fields only.
- `include_json_pointers` compares only explicitly named shared contract fields; missing fields still fail. Use this when the POC and Ensemble expose intentionally different metadata, and document the selected contract fields in the scenario.
- `numeric_tolerances` maps JSON pointers to absolute numeric tolerances. It must not be used to paper over ranking, confidence, or model drift without an explicit quality rationale.
- Retrieval quality is evaluated separately with `python -m parity.retrieval_metrics` against a versioned judged corpus. It reports Recall@k, MRR@k, nDCG@k, candidate-minus-reference deltas, top-k Jaccard overlap, and Kendall tau over common IDs. The evaluator deliberately applies no pass/fail threshold until the judged corpus and acceptable regression policy have been reviewed. See [judged retrieval corpus format](../parity/corpora/README.md).

## Latest POC baseline

The capability map is pinned to POC `main` at `b1581e317d291bb9e16a12f42916640146f3ba5a` (2026-11-07). The latest POC changes add read-only session-history REST endpoints, prompt-ledger ingestion into Knowledge with credential-looking prompts filtered, and chunk provenance (`chunk_index`, `chunk_count`, `content_sha256`) on exact-match knowledge hits. The map records these as explicit parity work; prompt capture without vector/lexical indexing is not considered searchable prompt parity.

## Capability inventory

`parity/capability-map.json` records source-audited POC and Ensemble route mappings for all audited service areas, including Embedding, Vector, Lexical, Hybrid Search, Reranking, Chunking, Tokenization, Index, Extraction, Intent, Ranking, Knowledge, Memory, Graph, and Prompt Capture. A route mapping is not a parity result: capabilities without executed scenarios remain `mapped-unmeasured`. Memory is explicitly recorded as a private Unix-socket POC service, so it needs a transport adapter before it can participate in the HTTP runner.

## Current limitations

This increment provides the manifest-driven HTTP runner, explicit contract projections, deterministic comparison policy, machine-readable report, unit tests, and source-verified scenarios for five capabilities. It does not yet include the full POC-to-Ensemble route inventory, a shared judged retrieval corpus, live model/runtime comparisons, or a Control Center view. The retrieval metric engine is implemented, but the shared judged corpus and live POC/Ensemble result adapters remain outstanding. Those require source-audited scenario contracts and reference/candidate environment wiring. A capability with no executed scenario remains unmeasured, not complete.
