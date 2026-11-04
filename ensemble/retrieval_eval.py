from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


def _relevance_map(values: Mapping[str, Any], query: str) -> dict[str, float]:
    if not isinstance(values, Mapping):
        raise ValueError(f"qrels for query {query!r} must be a mapping")
    relevance: dict[str, float] = {}
    for identity, score in values.items():
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("relevance identities must be non-empty strings")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(float(score)) or score < 0:
            raise ValueError(f"relevance for {identity!r} must be finite and non-negative")
        relevance[identity] = float(score)
    return relevance


def recall_at_k(ranked: Sequence[str], relevance: Mapping[str, float], k: int) -> float:
    relevant = {identity for identity, score in relevance.items() if score > 0}
    if not relevant:
        return 0.0
    return len(relevant.intersection(ranked[:k])) / len(relevant)


def reciprocal_rank_at_k(ranked: Sequence[str], relevance: Mapping[str, float], k: int) -> float:
    for rank, identity in enumerate(ranked[:k], start=1):
        if relevance.get(identity, 0.0) > 0:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevance: Mapping[str, float], k: int) -> float:
    def gain(identity: str) -> float:
        return (2.0 ** relevance.get(identity, 0.0) - 1.0)

    dcg = sum(gain(identity) / math.log2(rank + 1) for rank, identity in enumerate(ranked[:k], start=1))
    ideal = sorted(relevance.values(), reverse=True)[:k]
    idcg = sum((2.0 ** score - 1.0) / math.log2(rank + 1) for rank, score in enumerate(ideal, start=1))
    return 0.0 if idcg == 0.0 else dcg / idcg


def evaluate_runs(
    qrels: Mapping[str, Mapping[str, Any]],
    runs: Mapping[str, Mapping[str, Sequence[str]]],
    *,
    k: int = 10,
) -> dict[str, Any]:
    """Evaluate ranked retrieval runs against query-level graded relevance judgments."""
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")
    if not isinstance(qrels, Mapping) or not isinstance(runs, Mapping):
        raise ValueError("qrels and runs must be mappings")
    normalized_qrels = {
        query: _relevance_map(values, query)
        for query, values in qrels.items()
    }
    output: dict[str, Any] = {"k": k, "systems": {}}
    for system, query_runs in runs.items():
        if not isinstance(system, str) or not system.strip() or not isinstance(query_runs, Mapping):
            raise ValueError("each run must have a non-empty system name and query mapping")
        per_query = {}
        for query, relevance in normalized_qrels.items():
            ranked = query_runs.get(query, [])
            if not isinstance(ranked, Sequence) or isinstance(ranked, (str, bytes, bytearray)):
                raise ValueError(f"ranked results for {system}/{query} must be a sequence")
            if any(not isinstance(identity, str) or not identity.strip() for identity in ranked):
                raise ValueError(f"ranked identities for {system}/{query} must be non-empty strings")
            per_query[query] = {
                f"recall@{k}": recall_at_k(ranked, relevance, k),
                f"mrr@{k}": reciprocal_rank_at_k(ranked, relevance, k),
                f"ndcg@{k}": ndcg_at_k(ranked, relevance, k),
            }
        query_count = len(per_query)
        macro = {
            metric: (
                sum(values[metric] for values in per_query.values()) / query_count
                if query_count else 0.0
            )
            for metric in (f"recall@{k}", f"mrr@{k}", f"ndcg@{k}")
        }
        output["systems"][system] = {"macro": macro, "per_query": per_query}
    return output
