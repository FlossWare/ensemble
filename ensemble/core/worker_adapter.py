from __future__ import annotations

import importlib
from typing import Any

from ..worker_runtime import WorkerOperation, WorkerRequest, WorkerRuntime
from .contracts import CapabilityDescriptor, RuntimeContext, RuntimeProfile


class WorkerOperationCapability:
    """Expose one legacy WorkerRuntime operation through the Full capability contract."""

    def __init__(self, runtime: WorkerRuntime, operation: WorkerOperation) -> None:
        self.runtime = runtime
        self.operation = operation
        self.descriptor = CapabilityDescriptor(
            name=f"worker-adapter.{operation.capability}.{operation.operation}",
            version=str(operation.contract_version),
            profiles=(RuntimeProfile.FULL,),
        )

    @property
    def key(self) -> str:
        return f"{self.operation.capability}.{self.operation.operation}"

    async def initialize(self, context: RuntimeContext) -> None:
        operations = context.services.setdefault("worker_operations", {})
        if self.key in operations:
            raise ValueError(f"worker operation already exposed: {self.key}")
        operations[self.key] = self

    async def close(self, context: RuntimeContext) -> None:
        operations = context.services.get("worker_operations", {})
        if operations.get(self.key) is self:
            del operations[self.key]

    async def health(self, context: RuntimeContext) -> dict[str, Any]:
        return {
            "status": "ready",
            "operation": self.key,
            "contract_version": self.operation.contract_version,
        }

    async def invoke(
        self,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
        deadline_unix_ms: int | None = None,
    ) -> dict[str, Any]:
        response = await self.runtime.invoke(WorkerRequest(
            request_id=request_id or __import__("uuid").uuid4().hex,
            capability=self.operation.capability,
            operation=self.operation.operation,
            payload=payload,
            deadline_unix_ms=deadline_unix_ms,
        ))
        return response.result


def load_worker_adapter_capabilities(adapter_spec: str) -> tuple[WorkerOperationCapability, ...]:
    """Load a trusted module:register adapter and expose each operation as a capability."""
    module_name, separator, factory_name = adapter_spec.strip().partition(":")
    if not separator or not module_name or not factory_name:
        raise ValueError("ENSEMBLE_WORKER_ADAPTER must use module:register format")
    runtime = WorkerRuntime()
    adapter = getattr(importlib.import_module(module_name), factory_name)
    if not callable(adapter):
        raise TypeError("configured worker adapter registration target must be callable")
    adapter(runtime)
    operations = runtime.registered_operations()
    if not operations:
        raise ValueError("configured worker adapter registered no operations")
    return tuple(WorkerOperationCapability(runtime, operation) for operation in operations)
