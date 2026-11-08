# Retrieval evaluation data

## Synthetic smoke fixture

`retrieval-smoke.json` is a small, deterministic corpus with five documents, five queries, and explicit graded relevance judgments. It exists to exercise the complete Ensemble ingestion and retrieval path and to catch regressions in ranking behavior.

Run it with:

```bash
pytest -q tests/test_retrieval_smoke.py
```

The test ingests the fixture into in-memory stores, executes the real hybrid-search REST endpoint, converts ranked results to source identities, and evaluates Recall@k, MRR@k, and NDCG@k using `ensemble.retrieval_eval`.

**This is not a production relevance benchmark.** The corpus and judgments are synthetic and intentionally small. Do not use its scores to tune production RRF weights, Thompson channel weights, embedding models, or rerankers. A production benchmark requires representative documents from the intended corpus, representative user queries, and relevance judgments reviewed by humans. The fixture is a regression smoke test, not evidence of production retrieval quality.

## Production evaluation requirements

A production qrels set should record:
- Stable document or chunk identities from the target corpus.
- Real queries sampled from the intended workload, with sensitive content removed.
- Graded relevance judgments and the judgment source/reviewer.
- Corpus snapshot/revision and embedding model identity.
- Separate runs for lexical, vector, hybrid/RRF, and reranked retrieval.

Keep evaluation runs separate from qrels so multiple retrieval systems can be compared against the same judgments.

Start a real benchmark from `production-qrels.template.json`. This file is intentionally blank: no query, relevance grade, or reviewer identity is pre-filled. Populate:

- `benchmark` with a stable corpus snapshot/revision, embedding model, written judgment policy, judgment scale, and actual reviewers/dates.
- `queries` with stable query IDs and the privacy-reviewed query text and sampling provenance.
- `qrels` with one mapping per judged query from stable source/chunk IDs to human-assigned grades. Include explicit zero grades only when the judgment policy calls for them; absence of an ID must not be silently interpreted as a negative judgment.
- `runs` with independent ranked IDs from lexical, vector, hybrid/RRF, and reranked systems, using the same query IDs and corpus snapshot.

After the judgments are complete, run `python scripts/evaluate-retrieval.py path/to/production-qrels.json`. Keep the original judgment file immutable and versioned; create a new snapshot when judgments or corpus identity change. Do not score an empty or partially judged template as a production benchmark.


## Memory channel smoke comparison

The same fixture is also exercised through Memory's REST keyword, semantic, and
hybrid endpoints by `tests/test_memory.py`. The test calculates Recall@k,
MRR@k, and nDCG@k for each channel using the fixture's explicit graded
judgments. This verifies that the three paths execute and remain measurable
against one shared qrels set.

This comparison is deliberately limited to the five-document synthetic smoke
corpus. Its metrics are regression signals only; they are not representative
of user Memory workloads and must not be used to select production ranking
weights or claim retrieval quality. The production acceptance criterion remains
open until representative corpus snapshots, real workload queries, and
human-reviewed relevance judgments are available.
