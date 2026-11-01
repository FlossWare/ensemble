from __future__ import annotations

import hashlib
import re
from typing import Any


MAX_TEXT_CHARS = 4_000_000
MAX_SOURCE_ID_CHARS = 512
MEDIA_TYPES = ("text/plain", "text/markdown")
PROFILES = {
    "window-v1": {"version": "1", "strategy": "window", "size": 1800, "overlap": 200},
    "markdown-v1": {"version": "1", "strategy": "markdown", "size": 2000},
    "message-v1": {"version": "1", "strategy": "message", "size": 20000},
}
HEADING = re.compile(r"^(#{1,6})\\s+(.*)$")


class ChunkingError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _chunk_id(profile: str, source_id: str, start: int, end: int) -> str:
    key = f"{profile}|{PROFILES[profile]['version']}|{source_id}|{start}|{end}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _windows(text: str, size: int, overlap: int) -> list[tuple[int, int]]:
    if not text:
        return []
    spans: list[tuple[int, int]] = []
    start, step = 0, size - overlap
    while True:
        end = min(start + size, len(text))
        spans.append((start, end))
        if end >= len(text):
            return spans
        start += step


def _markdown_sections(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    line_starts: list[int] = []
    position = 0
    for line in text.splitlines(keepends=True):
        line_starts.append(position)
        position += len(line)
    headings = []
    for offset, line in zip(line_starts, text.splitlines()):
        match = HEADING.match(line)
        if match:
            headings.append((offset, match.group(2).strip()))
    if not headings or headings[0][0] > 0:
        headings.insert(0, (0, ""))
    for index, (offset, header) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
        if end > offset:
            spans.append((offset, end, header))
    return spans


def chunk(profile_name: Any, source_id: Any, text: Any, media_type: Any = "text/plain") -> list[dict[str, Any]]:
    if profile_name not in PROFILES:
        raise ChunkingError("unsupported_profile", f"unknown chunking profile: {profile_name}", 422)
    if not isinstance(source_id, str) or not source_id or len(source_id) > MAX_SOURCE_ID_CHARS:
        raise ChunkingError("invalid_request", "source_id must be a non-empty string of at most 512 characters")
    if media_type not in MEDIA_TYPES:
        raise ChunkingError("unsupported_media_type", "media_type must be text/plain or text/markdown", 415)
    if not isinstance(text, str):
        raise ChunkingError("invalid_request", "text must be a string")
    if len(text) > MAX_TEXT_CHARS:
        raise ChunkingError("payload_too_large", f"text is limited to {MAX_TEXT_CHARS} characters", 413)

    spec = PROFILES[profile_name]
    if spec["strategy"] == "window":
        spans = [(start, end, "") for start, end in _windows(text, spec["size"], spec["overlap"])]
    elif spec["strategy"] == "message":
        spans = [(0, min(len(text), spec["size"]), "")] if text else []
    else:
        sections = _markdown_sections(text) if media_type == "text/markdown" else [(0, len(text), "")]
        spans = []
        for start, end, header in sections:
            section_length = end - start
            if section_length <= spec["size"]:
                spans.append((start, end, header))
            else:
                spans.extend(
                    (start + left, start + right, header)
                    for left, right in _windows(text[start:end], spec["size"], 0)
                )

    records = []
    for index, (start, end, header) in enumerate(spans):
        if end <= start:
            continue
        records.append({
            "chunk_id": _chunk_id(profile_name, source_id, start, end),
            "index": index,
            "start": start,
            "end": end,
            "header": header,
            "text": text[start:end],
        })
    return records


def describe_profiles() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "version": spec["version"],
            "parameters": {key: value for key, value in spec.items() if key != "version"},
        }
        for name, spec in sorted(PROFILES.items())
    ]


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
