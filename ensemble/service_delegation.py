from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class ServiceRequest(BaseModel):
    contract_version: Literal[1] = 1
    request_id: UUID = Field(default_factory=uuid4)
    requester_instance_id: str = Field(min_length=1, max_length=128)
    target_capability: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    deadline: datetime | None = None
    correlation_id: str | None = Field(default=None, max_length=256)

    @field_validator("deadline")
    @classmethod
    def require_timezone(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("deadline must include a timezone")
        return value


class ServiceResponse(BaseModel):
    contract_version: Literal[1] = 1
    request_id: UUID
    provider_instance_id: str
    operation: str
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    result: dict[str, Any]
