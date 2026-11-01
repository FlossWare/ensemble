from __future__ import annotations


class Chunker:
    """Deterministic character-window chunker; token-aware strategies can be plugged in later."""

    def split(self, text: str, target_characters: int = 1200, overlap_characters: int = 160) -> list[str]:
        if target_characters < 1 or overlap_characters < 0 or overlap_characters >= target_characters:
            raise ValueError("overlap must be non-negative and smaller than the chunk target")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return []

        chunks: list[str] = []
        start = 0
        while start < len(normalized):
            end = min(start + target_characters, len(normalized))
            if end < len(normalized):
                boundary = max(normalized.rfind(" ", start, end), normalized.rfind("\n", start, end))
                if boundary > start + target_characters // 2:
                    end = boundary
            chunk = normalized[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(normalized):
                break
            start = max(start + 1, end - overlap_characters)
            while start < len(normalized) and normalized[start].isspace():
                start += 1
        return chunks
