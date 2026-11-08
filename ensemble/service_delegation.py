from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


ServiceHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


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


class ServiceInvocation(BaseModel):
    """Caller-facing request; requester identity is assigned by the local peer adapter."""
    contract_version: Literal[1] = 1
    request_id: UUID = Field(default_factory=uuid4)
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


class ServiceOperationRegistry:
    """Explicit catalogue of bounded peer-call operations backed by domain services."""

    def __init__(self) -> None:
        self._operations: dict[tuple[str, str], tuple[str, dict[str, Any], ServiceHandler]] = {}

    def register(
        self,
        capability: str,
        operation: str,
        description: str,
        payload_schema: dict[str, Any],
        handler: ServiceHandler,
    ) -> None:
        key = (capability, operation)
        if not capability or not operation or key in self._operations:
            raise ValueError("service capability/operation must be unique and non-empty")
        if not callable(handler):
            raise TypeError("service handler must be callable")
        self._operations[key] = (description, payload_schema, handler)

    def describe(self) -> dict[str, Any]:
        return {
            "contract_version": 1,
            "operations": [
                {
                    "capability": capability,
                    "operation": operation,
                    "description": details[0],
                    "payload_schema": details[1],
                }
                for (capability, operation), details in sorted(self._operations.items())
            ],
        }

    def resolve(self, capability: str, operation: str) -> ServiceHandler | None:
        entry = self._operations.get((capability, operation))
        return entry[2] if entry else None

    async def invoke(
        self, capability: str, operation: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        handler = self.resolve(capability, operation)
        if handler is None:
            raise KeyError(f"unsupported service operation: {capability}.{operation}")
        result = await handler(payload)
        if not isinstance(result, dict):
            raise TypeError("service operation must return an object")
        return result
