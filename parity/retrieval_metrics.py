"""Evaluate POC and Ensemble retrieval rankings against a shared judged corpus.

No pass/fail thresholds are implied. This module reports measured quality and
ranking agreement so capability owners can set evidence-based gates later.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any


def _validate_ranking(ranking: list[str], label: str) -> None:
    if not isinstance(ranking, list) or any(not isinstance(item, str) or not item for item in ranking):
        raise ValueError(f"{label} must be a list of non-empty document IDs")
    if len(ranking) != len(set(ranking)):
        raise ValueError(f"{label} contains duplicate document IDs")


def recall_at_k(relevance: dict[str, float], ranking: list[str], k: int) -> float:
    relevant = {doc for doc, score in relevance.items() if score > 0}
    if not relevant:
        raise ValueError("each judged query must have at least one relevant document")
    return len(relevant.intersection(ranking[:k])) / len(relevant)


def reciprocal_rank_at_k(relevance: dict[str, float], ranking: list[str], k: int) -> float:
    for index, doc in enumerate(ranking[:k], start=1):
        if relevance.get(doc, 0) > 0:
            return 1.0 / index
    return 0.0


def ndcg_at_k(relevance: dict[str, float], ranking: list[str], k: int) -> float:
    def dcg(scores: list[float]) -> float:
        return sum((2 ** score - 1) / math.log2(index + 2)
                   for index, score in enumerate(scores))

    actual = dcg([float(relevance.get(doc, 0)) for doc in ranking[:k]])
    ideal = dcg(sorted((float(score) for score in relevance.values()), reverse=True)[:k])
    return actual / ideal if ideal else 0.0


def top_k_jaccard(left: list[str], right: list[str], k: int) -> float:
    a, b = set(left[:k]), set(right[:k])
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def kendall_tau_common(left: list[str], right: list[str]) -> float:
    """Kendall tau over the common result IDs, ignoring non-overlapping IDs."""
    right_positions = {doc: index for index, doc in enumerate(right)}
    common = [doc for doc in left if doc in right_positions]
    if len(common) < 2:
        return 1.0
    concordant = discordant = 0
    for index, first in enumerate(common):
        for second in common[index + 1:]:
            if right_positions[first] < right_positions[second]:
                concordant += 1
            else:
                discordant += 1
    total = concordant + discordant
    return (concordant - discordant) / total if total else 1.0


def evaluate_corpus(corpus: dict[str, Any]) -> dict[str, Any]:
    if corpus.get("schema_version") != 1:
        raise ValueError("retrieval corpus schema_version must be 1")
    k_values = corpus.get("k_values", [5, 10])
    if not k_values or any(not isinstance(k, int) or isinstance(k, bool) or k < 1 for k in k_values):
        raise ValueError("k_values must contain positive integers")
    if len(k_values) != len(set(k_values)):
        raise ValueError("k_values must be unique")
    queries = corpus.get("queries", [])
    if not queries:
        raise ValueError("retrieval corpus must contain at least one judged query")

    per_query = []
    seen_query_ids = set()
    for query in queries:
        query_id = query.get("id")
        relevance = query.get("relevance")
        if not isinstance(query_id, str) or not query_id or query_id in seen_query_ids:
            raise ValueError("each query requires a unique non-empty id")
        seen_query_ids.add(query_id)
        if not isinstance(relevance, dict) or not relevance:
            raise ValueError(f"query {query_id}: relevance must be a non-empty mapping")
        relevance = {str(doc): float(score) for doc, score in relevance.items()}
        if any(score < 0 for score in relevance.values()):
            raise ValueError(f"query {query_id}: relevance scores cannot be negative")
        if not any(score > 0 for score in relevance.values()):
            raise ValueError(f"query {query_id}: at least one document must be relevant")
        reference = query.get("reference", [])
        candidate = query.get("candidate", [])
        _validate_ranking(reference, f"query {query_id} reference")
        _validate_ranking(candidate, f"query {query_id} candidate")

        row: dict[str, Any] = {"id": query_id, "systems": {}, "agreement": {}}
        for system, ranking in (("reference", reference), ("candidate", candidate)):
            row["systems"][system] = {}
            for k in k_values:
                row["systems"][system][str(k)] = {
                    "recall": recall_at_k(relevance, ranking, k),
                    "mrr": reciprocal_rank_at_k(relevance, ranking, k),
                    "ndcg": ndcg_at_k(relevance, ranking, k),
                }
        for k in k_values:
            row["agreement"][str(k)] = {
                "top_k_jaccard": top_k_jaccard(reference, candidate, k),
                "kendall_tau_common": kendall_tau_common(reference[:k], candidate[:k]),
            }
        per_query.append(row)

    aggregate: dict[str, Any] = {}
    for k in k_values:
        key = str(k)
        aggregate[key] = {}
        for system in ("reference", "candidate"):
            aggregate[key][system] = {
                metric: sum(row["systems"][system][key][metric] for row in per_query) / len(per_query)
                for metric in ("recall", "mrr", "ndcg")
            }
        aggregate[key]["delta_candidate_minus_reference"] = {
            metric: aggregate[key]["candidate"][metric] - aggregate[key]["reference"][metric]
            for metric in ("recall", "mrr", "ndcg")
        }
        aggregate[key]["agreement"] = {
            metric: sum(row["agreement"][key][metric] for row in per_query) / len(per_query)
            for metric in ("top_k_jaccard", "kendall_tau_common")
        }

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus": corpus.get("name", "unnamed"),
        "query_count": len(per_query),
        "k_values": k_values,
        "thresholds_applied": False,
        "overall": "MEASURED",
        "aggregate": aggregate,
        "queries": per_query,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate retrieval rankings against judged relevance.")
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--report", type=Path, default=Path("retrieval-parity-report.json"))
    args = parser.parse_args(argv)
    report = evaluate_corpus(json.loads(args.corpus.read_text(encoding="utf-8")))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"overall": report["overall"], "query_count": report["query_count"],
                      "report": str(args.report)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
