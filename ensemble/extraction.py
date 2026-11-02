"""Deterministic entity candidate extraction with offsets and provenance."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

MAX_TEXT_CHARS = 1_000_000
CONFIDENCE_EXACT = 1.0
CONFIDENCE_UNCERTAIN = 0.4

PATTERNS = [
    ("url", re.compile(r"""https?://[^\s<>"')]+"""), True),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE), True),
    ("uuid", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE), True),
    ("issue_key", re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,7}\b"), True),
    ("path", re.compile(r"(?<![\w/])(?:~|\.{1,2})?/[\w.\-/]+|(?<![\w/])[\w.\-]+(?:/[\w.\-]+)+"), True),
    ("file", re.compile(r"\b[\w\-]+\.(?:py|js|ts|sh|md|json|yaml|yml|toml|txt|java|go|rs|c|h|sql|service)\b", re.IGNORECASE), True),
    ("version", re.compile(r"\bv?\d+\.\d+(?:\.\d+)+\b"), True),
    ("technical_name", re.compile(r"\b[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+\b"), True),
    ("camel_case_name", re.compile(r"\b[A-Z][a-z]+[A-Z][A-Za-z0-9]*\b"), False),
    ("capitalised_name", re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b"), False),
]


def normalize_entity(kind: str, value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if kind in {"url", "email", "technical_name", "file", "path", "issue_key"}:
        normalized = normalized.casefold()
    else:
        normalized = re.sub(r"\s+", " ", normalized).casefold()
    return normalized


def extract(text: Any, source: str | None = None) -> list[dict[str, Any]]:
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if len(text) > MAX_TEXT_CHARS:
        raise OverflowError(f"text is limited to {MAX_TEXT_CHARS} characters")

    found: list[dict[str, Any]] = []
    taken: list[tuple[int, int]] = []
    for kind, pattern, exact in PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(not (end <= left or start >= right) for left, right in taken):
                continue
            if exact:
                taken.append((start, end))
            value = match.group(0)
            normalized = normalize_entity(kind, value)
            found.append({
                "type": kind,
                "text": value,
                "normalized": normalized,
                "candidate_id": "candidate_" + hashlib.sha256(f"{kind}:{normalized}".encode("utf-8")).hexdigest()[:24],
                "start": start,
                "end": end,
                "exact": exact,
                "confidence": CONFIDENCE_EXACT if exact else CONFIDENCE_UNCERTAIN,
                "provenance": {"source": source, "start": start, "end": end} if source else {"start": start, "end": end},
            })

    found.sort(key=lambda entity: (entity["start"], entity["end"], entity["type"]))
    return found
