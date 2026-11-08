"""Shared runtime contracts and lifecycle primitives for Ensemble."""

from .contracts import Capability, CapabilityDescriptor, RuntimeContext, RuntimeProfile
from .lifecycle import CapabilityError, CapabilityLifecycle, CapabilityRegistry, StoreCapability
from .worker_adapter import WorkerOperationCapability, load_worker_adapter_capabilities

__all__ = [
    "Capability",
    "CapabilityDescriptor",
    "CapabilityError",
    "CapabilityLifecycle",
    "CapabilityRegistry",
    "RuntimeContext",
    "RuntimeProfile",
    "StoreCapability",
    "WorkerOperationCapability",
    "load_worker_adapter_capabilities",
]
