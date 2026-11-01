"""Explainable rule-based query intent classification."""

from __future__ import annotations

import re
from typing import Any


MAX_TEXT_CHARS = 20_000
RULES = {
    "history": [
        r"\bwhat did we\b", r"\blast time\b", r"\bearlier\b",
        r"\bwe (?:decided|discussed|did)\b", r"\bremember\b",
        r"\bpreviously\b", r"\bwhat have (?:we|you)\b",
    ],
    "debug": [
        r"\bwhy (?:is|does|did|isn'?t|doesn'?t|am i)\b",
        r"\b(?:error|fails?|failing|broken|crash\w*|exception|timeout)\b",
        r"\bnot working\b", r"\bwhat'?s wrong\b",
    ],
    "howto": [
        r"\bhow (?:do|does|can|should|to)\b",
        r"\bsteps? to\b", r"\bexample of\b",
    ],
    "lookup": [
        r"\bwhat (?:is|are)\b", r"\bwhere (?:is|are|do)\b",
        r"\bwhich\b", r"\bwho\b", r"\bshow me\b",
    ],
    "change": [
        r"\b(?:implement|add|build|fix|refactor|rename|remove|delete|update|change)\b",
        r"\bcan you (?:make|write|create)\b",
    ],
}
COMPILED = {
    label: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    for label, patterns in RULES.items()
}


def classify(text: Any) -> dict[str, Any]:
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if len(text) > MAX_TEXT_CHARS:
        raise OverflowError(f"text is limited to {MAX_TEXT_CHARS} characters")

    evidence: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    for label, patterns in COMPILED.items():
        for pattern in patterns:
            for match in pattern.finditer(text):
                counts[label] = counts.get(label, 0) + 1
                evidence.append({"rule": label, "text": match.group(0)})

    if not counts:
        return {"label": "other", "confidence": 0.0, "evidence": [], "scores": {}}

    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    label, top = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    total = sum(counts.values())
    confidence = round((top / total) * (top / (top + runner_up)), 3)
    return {
        "label": label,
        "confidence": confidence,
        "evidence": evidence,
        "scores": dict(ranked),
    }
