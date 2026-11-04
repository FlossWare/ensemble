"""Versioned deterministic tokenization profiles migrated from the POC contract."""

from __future__ import annotations

import re
from typing import Any


MAX_TEXT_CHARS = 2_000_000
PROFILES = {"lexical-v1": {"version": "1", "min_length": 2}}

# Compound identifiers retain their complete spelling and also emit component terms.
COMPOUND = re.compile(r"[^\W_]+(?:[._:\-/][^\W_]+)+", re.UNICODE)
SIMPLE = re.compile(r"[^\W_]+", re.UNICODE)


class TokenizationError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def tokenize(profile: Any, text: Any) -> list[dict[str, Any]]:
    if profile not in PROFILES:
        raise TokenizationError(
            "unsupported_profile",
            f"unknown tokenization profile: {profile}",
            status=422,
        )
    if not isinstance(text, str):
        raise TokenizationError("invalid_request", "text must be a string")
    if len(text) > MAX_TEXT_CHARS:
        raise TokenizationError(
            "payload_too_large",
            f"text is limited to {MAX_TEXT_CHARS} characters",
            status=413,
        )

    min_length = PROFILES[profile]["min_length"]
    folded = text.casefold()
    if len(folded) != len(text):
        # Preserve code-point offsets into the original text.
        folded = "".join(
            char.casefold() if len(char.casefold()) == 1 else char.lower()
            for char in text
        )

    tokens: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []
    for match in COMPOUND.finditer(folded):
        term = match.group(0)
        if len(term) >= min_length:
            tokens.append({
                "term": term,
                "start": match.start(),
                "end": match.end(),
                "kind": "whole",
            })
            spans.append((match.start(), match.end()))

    for match in SIMPLE.finditer(folded):
        term = match.group(0)
        if len(term) < min_length:
            continue
        inside_whole = any(
            start <= match.start() and match.end() <= end
            for start, end in spans
        )
        if inside_whole and (match.start(), match.end()) in spans:
            continue
        tokens.append({
            "term": term,
            "start": match.start(),
            "end": match.end(),
            "kind": "part" if inside_whole else "whole",
        })

    tokens.sort(key=lambda token: (token["start"], token["end"], token["kind"]))
    return tokens


def describe_profiles() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "version": spec["version"],
            "parameters": {"min_length": spec["min_length"]},
        }
        for name, spec in sorted(PROFILES.items())
    ]
