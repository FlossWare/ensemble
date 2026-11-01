from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID
from .models import Document, Chunk


@dataclass(frozen=True)
class IndexedChunk:
    document: Document
    chunk: Chunk


class DocumentStore(Protocol):
    async def save(self, document: Document, chunks: list[Chunk]) -> None: ...
    async def all_chunks(self) -> list[IndexedChunk]: ...


class InMemoryDocumentStore:
    """Development/test store. It is intentionally non-durable."""

    def __init__(self):
        self._documents: dict[tuple[str, int], Document] = {}
        self._chunks: dict[UUID, list[Chunk]] = {}

    async def save(self, document: Document, chunks: list[Chunk]) -> None:
        key = (document.source_id, document.revision)
        self._documents[key] = document
        self._chunks[document.id] = list(chunks)

    async def all_chunks(self) -> list[IndexedChunk]:
        result: list[IndexedChunk] = []
        for document_id, chunks in self._chunks.items():
            document = next((d for d in self._documents.values() if d.id == document_id), None)
            if document is not None:
                result.extend(IndexedChunk(document, chunk) for chunk in chunks)
        return result
