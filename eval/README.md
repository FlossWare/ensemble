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
