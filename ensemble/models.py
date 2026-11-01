from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, Field
from uuid import UUID, uuid4


class DocumentIn(BaseModel):
    source_id: str = Field(min_length=1, max_length=1024)
    revision: int = Field(default=1, ge=1)
    title: str = Field(min_length=1, max_length=2048)
    content: str = Field(min_length=1)


class DocumentOut(BaseModel):
    id: UUID
    source_id: str
    revision: int
    partition_id: str
    title: str
    chunk_count: int
    embedded: bool
    created_at: datetime


class SearchIn(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=100)


class SearchHit(BaseModel):
    document_id: UUID
    source_id: str
    revision: int
    title: str
    chunk_id: UUID
    chunk_ordinal: int
    text: str
    lexical_score: float
    vector_score: float
    fusion_score: float


class Document:
    def __init__(
        self, source_id: str, revision: int, title: str, content: str,
        partition_id: str = "default",
    ):
        self.id = uuid4()
        self.source_id = source_id.strip()
        self.revision = revision
        self.title = title.strip()
        self.content = content
        self.partition_id = partition_id
        self.created_at = datetime.now(timezone.utc)


class Chunk:
    def __init__(
        self, document_id: UUID, ordinal: int, content: str,
        embedding: list[float] | None = None,
    ):
        self.id = uuid4()
        self.document_id = document_id
        self.ordinal = ordinal
        self.content = content
        self.embedding = embedding or []
