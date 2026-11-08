from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class ContextTokenCounter(Protocol):
    tokenizer_id: str
    exact: bool
    description: str

    def count(self, text: str) -> int: ...
    def truncate(self, text: str, max_tokens: int) -> str: ...


class ApproximateContextTokenCounter:
    tokenizer_id = "approx-char4"
    exact = False
    description = "UTF-8 character count divided by four; approximate, not model-tokenizer exact"

    def count(self, text: str) -> int:
        return (len(text) + 3) // 4

    def truncate(self, text: str, max_tokens: int) -> str:
        if max_tokens <= 0:
            return ""
        return text[:max_tokens * 4].rstrip()


class TokenizersContextTokenCounter:
    exact = True

    def __init__(self, tokenizer_path: str, tokenizer_id: str | None = None):
        try:
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RuntimeError(
                "ENSEMBLE_CONTEXT_TOKENIZER_PATH requires the optional 'context-tokenizer' extra"
            ) from exc
        path = Path(tokenizer_path).expanduser()
        if not path.is_file():
            raise RuntimeError("configured context tokenizer file does not exist")
        self.tokenizer = Tokenizer.from_file(str(path))
        self.tokenizer_id = tokenizer_id or path.name
        self.description = f"exact token count using configured tokenizer {self.tokenizer_id}"

    def count(self, text: str) -> int:
        return len(self.tokenizer.encode(text, add_special_tokens=False).ids)

    def truncate(self, text: str, max_tokens: int) -> str:
        if max_tokens <= 0:
            return ""
        ids = self.tokenizer.encode(text, add_special_tokens=False).ids
        while max_tokens > 0:
            candidate = self.tokenizer.decode(ids[:max_tokens], skip_special_tokens=True)
            if self.count(candidate) <= max_tokens:
                return candidate.rstrip()
            max_tokens -= 1
        return ""


def configured_context_token_counter() -> ContextTokenCounter:
    path = os.getenv("ENSEMBLE_CONTEXT_TOKENIZER_PATH", "").strip()
    if not path:
        return ApproximateContextTokenCounter()
    tokenizer_id = os.getenv("ENSEMBLE_CONTEXT_TOKENIZER_ID", "").strip() or None
    return TokenizersContextTokenCounter(path, tokenizer_id)
