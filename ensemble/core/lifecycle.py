from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contracts import Capability, CapabilityDescriptor, RuntimeContext, RuntimeProfile


class CapabilityError(RuntimeError):
    pass


@dataclass(frozen=True)
class CapabilityStatus:
    name: str
    state: str
    detail: str | None = None


class CapabilityRegistry:
    """Validates capability identities and resolves a profile's dependency graph."""

    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        descriptor = capability.descriptor
        if not isinstance(descriptor, CapabilityDescriptor):
            raise TypeError("capability.descriptor must be CapabilityDescriptor")
        if descriptor.name in self._capabilities:
            raise CapabilityError(f"capability already registered: {descriptor.name}")
        self._capabilities[descriptor.name] = capability

    def get(self, name: str) -> Capability:
        try:
            return self._capabilities[name]
        except KeyError as exc:
            raise CapabilityError(f"unknown capability: {name}") from exc

    def resolve(self, profile: RuntimeProfile) -> tuple[Capability, ...]:
        selected = {
            name: capability
            for name, capability in self._capabilities.items()
            if profile in capability.descriptor.profiles
        }
        ordered: list[Capability] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                raise CapabilityError(f"capability dependency cycle at: {name}")
            capability = selected.get(name)
            if capability is None:
                raise CapabilityError(
                    f"required capability is unavailable for {profile.value}: {name}"
                )
            visiting.add(name)
            for dependency in capability.descriptor.requires:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)
            ordered.append(capability)

        for name in sorted(selected):
            visit(name)
        return tuple(ordered)


class CapabilityLifecycle:
    """Starts capabilities in dependency order and closes them in reverse order."""

    def __init__(self, registry: CapabilityRegistry, context: RuntimeContext) -> None:
        self.registry = registry
        self.context = context
        self._started: list[Capability] = []

    @property
    def started(self) -> tuple[str, ...]:
        return tuple(item.descriptor.name for item in self._started)

    async def start(self) -> None:
        if self._started:
            raise CapabilityError("capability lifecycle has already been started")
        plan = self.registry.resolve(self.context.profile)
        try:
            for capability in plan:
                await capability.initialize(self.context)
                self._started.append(capability)
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        failures: list[str] = []
        while self._started:
            capability = self._started.pop()
            try:
                await capability.close(self.context)
            except Exception as exc:
                failures.append(f"{capability.descriptor.name}: {type(exc).__name__}: {exc}")
        if failures:
            raise CapabilityError("capability shutdown failures: " + "; ".join(failures))

    async def health(self) -> list[dict[str, object]]:
        results = []
        for capability in self._started:
            try:
                result = await capability.health(self.context)
                results.append({"capability": capability.descriptor.name, **result})
            except Exception as exc:
                results.append({
                    "capability": capability.descriptor.name,
                    "status": "unhealthy",
                    "error": f"{type(exc).__name__}: {exc}",
                })
        return results
