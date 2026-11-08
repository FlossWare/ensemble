"""Shared runtime contracts and lifecycle primitives for Ensemble."""

from .contracts import Capability, CapabilityDescriptor, RuntimeContext, RuntimeProfile
from .lifecycle import CapabilityError, CapabilityLifecycle, CapabilityRegistry

__all__ = [
    "Capability",
    "CapabilityDescriptor",
    "CapabilityError",
    "CapabilityLifecycle",
    "CapabilityRegistry",
    "RuntimeContext",
    "RuntimeProfile",
]
