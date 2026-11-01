from __future__ import annotations

import re


TOKENIZER_ID = "ensemble-unicode-v1"


def tokenize(text: str) -> list[str]:
    """Deterministic Unicode word/punctuation tokenizer, not a model BPE tokenizer."""
    return re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE)
