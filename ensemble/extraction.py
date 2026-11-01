"""Exact identifier and uncertainty-aware entity extraction."""

from __future__ import annotations

import re
from typing import Any


MAX_TEXT_CHARS = 1_000_000
CONFIDENCE_EXACT = 1.0
CONFIDENCE_UNCERTAIN = 0.4

PATTERNS = [
    ("url", re.compile(r"https?://[^\s<>\\"')]+"), True),
    ("uuid", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), True),
    ("issue_key", re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,7}\b"), True),
    ("path", re.compile(r"(?<![\w/])(?:~|\.{1,2})?/[\w.\-/]+|(?<![\w/])[\w.\-]+(?:/[\w.\-]+)+"), True),
    ("file", re.compile(r"\b[\w\-]+\.(?:py|js|ts|sh|md|json|yaml|yml|toml|txt|java|go|rs|c|h|sql|service)\b"), True),
    ("version", re.compile(r"\bv?\d+\.\d+(?:\.\d+)+\b"), True),
    ("technical_name", re.compile(r"\b[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+\b"), True),
    ("capitalised_name", re.compile(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b"), False),
]


def extract(text: Any) -> list[dict[str, Any]]:
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if len(text) > MAX_TEXT_CHARS:
        raise OverflowError(f"text is limited to {MAX_TEXT_CHARS} characters")

    found: list[dict[str, Any]] = []
    taken: list[tuple[int, int]] = []
    for kind, pattern, exact in PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.start(), match.end()
            if any(not (end <= left or start >= right) for left, right in taken):
                continue
            if exact:
                taken.append((start, end))
            found.append({
                "type": kind,
                "text": match.group(0),
                "start": start,
                "end": end,
                "exact": exact,
                "confidence": CONFIDENCE_EXACT if exact else CONFIDENCE_UNCERTAIN,
            })

    found.sort(key=lambda entity: (entity["start"], entity["end"]))
    return found
