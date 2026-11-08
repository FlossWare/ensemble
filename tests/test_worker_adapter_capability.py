from __future__ import annotations

import asyncio
import sys
from types import ModuleType

from fastapi.testclient import TestClient

from ensemble.store import InMemoryDocumentStore
from ensemble.core import (
    CapabilityLifecycle,
    CapabilityRegistry,
    RuntimeContext,
    RuntimeProfile,
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
        assert await exposed.invoke({"text": "ensemble"}) == {"vector": [8.0]}
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



def test_configured_module_adapter_loader_registers_operations(monkeypatch):
    import sys
    from types import ModuleType

    from ensemble.core import load_worker_adapter_capabilities

    module = ModuleType("test_ensemble_adapter")

    def register(runtime):
        runtime.register(
            "reranking",
            "score",
            lambda payload: {"scores": [float(len(payload["query"]))]},
            description="Test reranker",
        )

    module.register = register
    monkeypatch.setitem(sys.modules, module.__name__, module)

    capabilities = load_worker_adapter_capabilities("test_ensemble_adapter:register")
    assert len(capabilities) == 1
    capability = capabilities[0]
    assert capability.descriptor.name == "worker-adapter.reranking.score"

    async def scenario():
        assert await capability.invoke({"query": "search"}) == {"scores": [6.0]}

    asyncio.run(scenario())



def test_full_api_loads_configured_worker_adapter_into_capability_context(monkeypatch):
    from ensemble.api import create_app

    module = ModuleType("test_ensemble_api_adapter")

    def register(runtime):
        runtime.register(
            "embeddings",
            "encode",
            lambda payload: {"length": len(payload["text"])},
        )

    module.register = register
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv("ENSEMBLE_WORKER_ADAPTER", "test_ensemble_api_adapter:register")

    app = create_app(
        profile=RuntimeProfile.FULL,
        store=InMemoryDocumentStore(),
        run_reclamation_worker=False,
    )

    with TestClient(app):
        assert "worker-adapter.embeddings.encode" in app.state.capability_lifecycle.started
        operation = app.state.runtime_context.services["worker_operations"]["embeddings.encode"]
        assert asyncio.run(operation.invoke({"text": "FlossWare"})) == {"length": 9}
