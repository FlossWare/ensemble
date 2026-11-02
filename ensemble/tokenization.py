from __future__ import annotations

import re


TOKENIZER_ID = "ensemble-unicode-v1"


def token_spans(text: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in re.finditer(r"\w+|[^\w\s]", text, flags=re.UNICODE)]


def tokenize(text: str) -> list[str]:
    """Deterministic Unicode word/punctuation tokenizer, not a model BPE tokenizer."""
    return [text[start:end] for start, end in token_spans(text)]


def split_by_tokens(text: str, target_tokens: int, overlap_tokens: int) -> list[str]:
    spans = token_spans(text)
    if not spans:
        return []
    if target_tokens < 1 or overlap_tokens < 0 or overlap_tokens >= target_tokens:
        raise ValueError("overlap_tokens must be non-negative and less than target_tokens")
    result = []
    step = target_tokens - overlap_tokens
    for start_index in range(0, len(spans), step):
        end_index = min(start_index + target_tokens, len(spans)) - 1
        result.append(text[spans[start_index][0]:spans[end_index][1]])
        if end_index == len(spans) - 1:
            break
    return result
