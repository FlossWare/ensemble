# Judged retrieval corpora

A corpus is versioned JSON with this shape:

```json
{
  "schema_version": 1,
  "name": "domain-and-date-specific-corpus",
  "k_values": [1, 3, 5, 10],
  "queries": [
    {
      "id": "query-001",
      "relevance": {"document-a": 3, "document-b": 1, "document-c": 0},
      "reference": ["document-a", "document-c", "document-b"],
      "candidate": ["document-b", "document-a", "document-c"]
    }
  ]
}
```

`relevance` is graded ground truth: zero is non-relevant, positive values express
judged relevance. `reference` and `candidate` are ordered document IDs returned
by the POC and Ensemble for the same query, configuration, corpus snapshot, and
model versions.

Run an evaluated corpus with:

```sh
python -m parity.retrieval_metrics parity/corpora/my-corpus.json \
  --report retrieval-parity-report.json
```

The report includes macro-averaged Recall@k, MRR@k, nDCG@k, candidate-minus-
reference deltas, top-k Jaccard overlap, and Kendall tau over common result IDs.
It does not apply pass/fail thresholds. A threshold is only appropriate after
the judged corpus, query population, relevance rubric, and acceptable quality
regression have been reviewed and versioned. Keep production data and sensitive
queries out of committed corpora unless explicitly approved.
