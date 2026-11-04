from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, Field
from uuid import UUID, uuid4


class DocumentIn(BaseModel):
    source_id: str = Field(min_length=1, max_length=1024)
    revision: int = Field(default=1, ge=1)
    title: str = Field(min_length=1, max_length=2048)
    content: str = Field(min_length=1)
    chunk_profile: str = Field(default="window-v1", min_length=1, max_length=64)
    media_type: str = Field(default="text/plain", max_length=128)
    metadata: dict = Field(default_factory=dict)


class DocumentOut(BaseModel):
    id: UUID
    source_id: str
    revision: int
    title: str
    chunk_count: int
    embedded: bool
    created_at: datetime
    chunk_profile: str = "window-v1"
    vector_indexed: bool = False
    metadata: dict = Field(default_factory=dict)


class SearchIn(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=500)
    rerank: bool | None = None


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
    graph_score: float = 0.0
    chunk_profile: str = "window-v1"
    chunk_start: int = 0
    chunk_end: int = 0
    chunk_header: str = ""
    rerank_score: float | None = None
    session_id: str | None = None
    project: str | None = None
    role: str | None = None
    kind: str | None = None
    is_subagent: bool | None = None
    text_sha: str | None = None


class Document:
    def __init__(self, source_id: str, revision: int, title: str, content: str, metadata: dict | None = None):
        self.id = uuid4()
        self.source_id = source_id.strip()
        self.revision = revision
        self.title = title.strip()
        self.content = content
        self.metadata = dict(metadata or {})
        self.created_at = datetime.now(timezone.utc)


class Chunk:
    def __init__(self, document_id: UUID, ordinal: int, content: str, embedding: list[float] | None = None, *, profile: str = "window-v1", start: int = 0, end: int = 0, header: str = ""):
        self.id = uuid4()
        self.document_id = document_id
        self.ordinal = ordinal
        self.content = content
        self.embedding = embedding or []
        self.profile = profile
        self.start = start
        self.end = end
        self.header = header
