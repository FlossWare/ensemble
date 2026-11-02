"""Deterministic reciprocal-rank fusion with explainable contributions."""

from __future__ import annotations

from typing import Any


DEFAULT_K = 60
MAX_LISTS = 8
MAX_CANDIDATES_PER_LIST = 2_000
MAX_TOP_K = 500


def fuse(lists: Any, top_k: Any = 10, k: Any = DEFAULT_K) -> list[dict[str, Any]]:
    if not isinstance(lists, dict) or not lists:
        raise ValueError("lists must be a non-empty object of named ranked lists")
    if len(lists) > MAX_LISTS:
        raise ValueError(f"at most {MAX_LISTS} lists")
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= MAX_TOP_K:
        raise ValueError(f"top_k must be an integer from 1 to {MAX_TOP_K}")

    scores: dict[str, float] = {}
    contributions: dict[str, dict[str, float]] = {}
    for name, items in lists.items():
        if not isinstance(name, str) or not isinstance(items, list):
            raise ValueError("each list must be a named array of {key, rank}")
        if len(items) > MAX_CANDIDATES_PER_LIST:
            raise ValueError(f"at most {MAX_CANDIDATES_PER_LIST} candidates per list")
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not item["key"]:
                raise ValueError("every candidate needs a non-empty string key")
            rank = item.get("rank")
            if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
                raise ValueError("every candidate needs a positive integer rank")
            if item["key"] in seen:
                continue
            seen.add(item["key"])
            share = 1.0 / (k + rank)
            scores[item["key"]] = scores.get(item["key"], 0.0) + share
            contributions.setdefault(item["key"], {})[name] = round(share, 6)

    order = sorted(scores, key=lambda key: (-scores[key], key))[:top_k]
    return [
        {
            "key": key,
            "score": round(scores[key], 6),
            "contributions": contributions[key],
        }
        for key in order
    ]
