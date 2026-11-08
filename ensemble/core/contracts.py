from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class RuntimeProfile(StrEnum):
    WORKER = "worker"
    STANDARD = "standard"
    FULL = "full"


@dataclass(frozen=True)
class CapabilityDescriptor:
    """Stable, implementation-neutral identity and dependency contract."""

    name: str
    version: str = "1"
    requires: tuple[str, ...] = ()
    profiles: tuple[RuntimeProfile, ...] = (
        RuntimeProfile.STANDARD,
        RuntimeProfile.FULL,
    )

    def __post_init__(self) -> None:
        if not self.name or self.name.strip() != self.name:
            raise ValueError("capability name must be a non-empty trimmed string")
        if not self.version:
            raise ValueError("capability version must not be empty")
        if self.name in self.requires:
            raise ValueError("a capability cannot require itself")
        if len(set(self.requires)) != len(self.requires):
            raise ValueError("capability requirements must be unique")


@dataclass
class RuntimeContext:
    """Services and deployment configuration shared during capability lifecycle."""

    profile: RuntimeProfile
    settings: dict[str, Any] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Capability(Protocol):
    descriptor: CapabilityDescriptor

    async def initialize(self, context: RuntimeContext) -> None: ...

    async def close(self, context: RuntimeContext) -> None: ...

    async def health(self, context: RuntimeContext) -> dict[str, Any]: ...
