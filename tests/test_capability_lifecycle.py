from __future__ import annotations

import asyncio

import pytest

from ensemble.core import (
    CapabilityDescriptor,
    CapabilityError,
    CapabilityLifecycle,
    CapabilityRegistry,
    RuntimeContext,
    RuntimeProfile,
)


class FakeCapability:
    def __init__(self, name, *, requires=(), profiles=None, events=None, fail=False):
        self.descriptor = CapabilityDescriptor(
            name=name,
            requires=tuple(requires),
            profiles=tuple(profiles or (RuntimeProfile.STANDARD, RuntimeProfile.FULL)),
        )
        self.events = events if events is not None else []
        self.fail = fail

    async def initialize(self, context):
        self.events.append(f"start:{self.descriptor.name}")
        if self.fail:
            raise RuntimeError("startup failed")

    async def close(self, context):
        self.events.append(f"stop:{self.descriptor.name}")

    async def health(self, context):
        return {"status": "ready"}


def test_registry_resolves_dependencies_before_dependents():
    events = []
    registry = CapabilityRegistry()
    registry.register(FakeCapability("retrieval", requires=("storage",), events=events))
    registry.register(FakeCapability("storage", events=events))
    plan = registry.resolve(RuntimeProfile.STANDARD)
    assert [item.descriptor.name for item in plan] == ["storage", "retrieval"]


def test_registry_excludes_capabilities_outside_profile():
    registry = CapabilityRegistry()
    registry.register(FakeCapability("base"))
    registry.register(FakeCapability("worker-only", profiles=(RuntimeProfile.WORKER,)))
    assert [item.descriptor.name for item in registry.resolve(RuntimeProfile.STANDARD)] == ["base"]


def test_registry_rejects_missing_profile_dependency_and_cycles():
    registry = CapabilityRegistry()
    registry.register(FakeCapability("full-only", profiles=(RuntimeProfile.FULL,)))
    registry.register(FakeCapability("standard", requires=("full-only",)))
    with pytest.raises(CapabilityError, match="unavailable"):
        registry.resolve(RuntimeProfile.STANDARD)

    cyclic = CapabilityRegistry()
    cyclic.register(FakeCapability("a", requires=("b",)))
    cyclic.register(FakeCapability("b", requires=("a",)))
    with pytest.raises(CapabilityError, match="cycle"):
        cyclic.resolve(RuntimeProfile.STANDARD)


def test_lifecycle_starts_in_order_and_stops_in_reverse():
    async def scenario():
    events = []
    registry = CapabilityRegistry()
    registry.register(FakeCapability("b", requires=("a",), events=events))
    registry.register(FakeCapability("a", events=events))
    lifecycle = CapabilityLifecycle(registry, RuntimeContext(RuntimeProfile.FULL))
    await lifecycle.start()
    assert lifecycle.started == ("a", "b")
    assert await lifecycle.health() == [
        {"capability": "a", "status": "ready"},
        {"capability": "b", "status": "ready"},
    ]
    await lifecycle.stop()
    assert events == ["start:a", "start:b", "stop:b", "stop:a"]
    asyncio.run(scenario())


def test_lifecycle_rolls_back_partial_startup():
    async def scenario():
        events = []
        registry = CapabilityRegistry()
        registry.register(FakeCapability("a", events=events))
        registry.register(FakeCapability("b", requires=("a",), events=events, fail=True))
        lifecycle = CapabilityLifecycle(registry, RuntimeContext(RuntimeProfile.FULL))
        with pytest.raises(RuntimeError, match="startup failed"):
            await lifecycle.start()
        assert lifecycle.started == ()
        assert events == ["start:a", "start:b", "stop:a"]
    asyncio.run(scenario())


def test_duplicate_capability_registration_is_rejected():
    registry = CapabilityRegistry()
    registry.register(FakeCapability("same"))
    with pytest.raises(CapabilityError, match="already registered"):
        registry.register(FakeCapability("same"))
