from __future__ import annotations

import asyncio

from ensemble.core import (
    CapabilityLifecycle,
    CapabilityRegistry,
    RuntimeContext,
    RuntimeProfile,
    load_worker_adapter_capabilities,
)
from ensemble.worker_runtime import WorkerRuntime


def test_worker_operations_join_full_capability_lifecycle_and_remain_invocable():
    async def scenario():
        worker = WorkerRuntime(worker_id="embedded-adapter")
        worker.register("embeddings", "encode", lambda payload: {
            "vector": [float(len(payload["text"]))],
        }, description="Encode text", contract_version=2)

        # The bridge factory is also exercised through a trusted module loader
        # in deployment; here the same operation contract is tested directly.
        from ensemble.core.worker_adapter import WorkerOperationCapability

        operation = worker.registered_operations()[0]
        capability = WorkerOperationCapability(worker, operation)
        registry = CapabilityRegistry()
        registry.register(capability)
        context = RuntimeContext(RuntimeProfile.FULL)
        lifecycle = CapabilityLifecycle(registry, context)

        await lifecycle.start()
        assert lifecycle.started == ("worker-adapter.embeddings.encode",)
        assert await lifecycle.health() == [{
            "capability": "worker-adapter.embeddings.encode",
            "status": "ready",
            "operation": "embeddings.encode",
            "contract_version": 2,
        }]

        exposed = context.services["worker_operations"]["embeddings.encode"]
        assert await exposed.invoke({"text": "ensemble"}) == {"vector": [9.0]}
        await lifecycle.stop()
        assert context.services["worker_operations"] == {}

    asyncio.run(scenario())


def test_worker_adapter_capability_is_full_profile_only():
    async def scenario():
        worker = WorkerRuntime()
        worker.register("provider", "complete", lambda payload: {"ok": True})
        from ensemble.core.worker_adapter import WorkerOperationCapability

        capability = WorkerOperationCapability(worker, worker.registered_operations()[0])
        registry = CapabilityRegistry()
        registry.register(capability)
        assert registry.resolve(RuntimeProfile.STANDARD) == ()
        assert len(registry.resolve(RuntimeProfile.FULL)) == 1

    asyncio.run(scenario())
