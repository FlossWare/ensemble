"""Explainable deterministic intent classification for caller-independent use."""

from __future__ import annotations

import re
from typing import Any

MAX_TEXT_CHARS = 20_000

RULES = {
    "history": [r"\bwhat did we\b", r"\blast time\b", r"\bearlier\b", r"\bwe (?:decided|discussed|did)\b", r"\bremember\b", r"\bpreviously\b", r"\bwhat have (?:we|you)\b"],
    "debug": [r"\bwhy (?:is|does|did|isn'?t|doesn'?t|am i)\b", r"\b(?:error|fails?|failing|broken|crash\w*|exception|timeout|traceback)\b", r"\bnot working\b", r"\bwhat'?s wrong\b"],
    "howto": [r"\bhow (?:do|does|can|should|to)\b", r"\bsteps? to\b", r"\bexample of\b", r"\bhow can i\b"],
    "lookup": [r"\bwhat (?:is|are)\b", r"\bwhere (?:is|are|do)\b", r"\bwhich\b", r"\bwho\b", r"\bshow me\b", r"\bfind\b", r"\blocate\b"],
    "change": [r"\b(?:implement|add|build|fix|refactor|rename|remove|delete|update|change|modify)\b", r"\bcan you (?:make|write|create)\b"],
    "create": [r"\b(?:draft|write|generate|design|create|produce)\b"],
    "compare": [r"\b(?:compare|versus|vs\.?|difference between|better than|trade.?offs?)\b"],
    "summarize": [r"\b(?:summari[sz]e|tl;dr|recap|overview)\b"],
    "explain": [r"\b(?:explain|why does|what does .* mean|meaning of)\b"],
    "plan": [r"\b(?:plan|roadmap|sequence|prioriti[sz]e|schedule)\b"],
    "analyze": [r"\b(?:analy[sz]e|evaluate|assess|review|investigate|diagnose)\b"],
}
COMPILED = {label: [re.compile(p, re.IGNORECASE) for p in patterns] for label, patterns in RULES.items()}
CAPABILITIES = {
    "history": ["memory"], "debug": ["search", "knowledge"], "howto": ["knowledge"],
    "lookup": ["search", "knowledge"], "change": ["planning"], "create": ["generation"],
    "compare": ["search", "ranking"], "summarize": ["memory", "knowledge"],
    "explain": ["knowledge"], "plan": ["planning", "memory"], "analyze": ["search", "ranking"],
}
CONSTRAINT_PATTERNS = {
    "limit": re.compile(r"\b(?:top|first|last|limit)\s+(\d{1,4})\b", re.IGNORECASE),
    "date": re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b"),
    "time_window": re.compile(r"\b(today|yesterday|this week|last week|this month|last month|recent|latest)\b", re.IGNORECASE),
    "negation": re.compile(r"\b(?:do not|don't|without|exclude|except)\s+([^,.!?;]{1,100})", re.IGNORECASE),
}


def classify(text: Any) -> dict[str, Any]:
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if len(text) > MAX_TEXT_CHARS:
        raise OverflowError(f"text is limited to {MAX_TEXT_CHARS} characters")

    counts: dict[str, int] = {}
    evidence: list[dict[str, Any]] = []
    for label, patterns in COMPILED.items():
        for pattern in patterns:
            for match in pattern.finditer(text):
                counts[label] = counts.get(label, 0) + 1
                evidence.append({"intent": label, "text": match.group(0), "start": match.start(), "end": match.end()})

    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    total = sum(counts.values())
    intents = []
    for index, (label, count) in enumerate(ranked):
        other = ranked[1][1] if index == 0 and len(ranked) > 1 else (ranked[0][1] if index > 0 else 0)
        confidence = round((count / total) * (count / (count + other)), 3) if total else 0.0
        intents.append({"label": label, "confidence": confidence, "evidence": [e for e in evidence if e["intent"] == label]})

    constraints = []
    for kind, pattern in CONSTRAINT_PATTERNS.items():
        for match in pattern.finditer(text):
            constraints.append({"type": kind, "value": match.group(1) if match.lastindex else match.group(0), "start": match.start(), "end": match.end()})

    primary = intents[0] if intents else {"label": "other", "confidence": 0.0, "evidence": []}
    required = sorted({cap for item in intents for cap in CAPABILITIES.get(item["label"], [])})
    return {
        "label": primary["label"],
        "confidence": primary["confidence"],
        "evidence": evidence,
        "scores": dict(ranked),
        "intents": intents,
        "constraints": constraints,
        "required_capabilities": required,
        "classification": "rule_based",
    }
