"""REST client used by the native terminal Control Center.

The terminal UI deliberately consumes public Ensemble HTTP contracts. It does
not import application services or inspect SQLite state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class ServiceSnapshot:
    health: dict[str, Any]
    readiness: dict[str, Any]
    configuration: dict[str, Any] | None = None


class ControlCenterClient:
    """Small synchronous client for read-only Control Center operations."""

    def __init__(self, base_url: str = "http://127.0.0.1:8090", timeout: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str) -> dict[str, Any]:
        response = httpx.get(f"{self.base_url}{path}", timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"Expected an object response from {path}")
        return payload

    def snapshot(self) -> ServiceSnapshot:
        """Fetch service health, readiness, and runtime summary."""
        health = self._get("/health")
        readiness = self._get("/ready")
        configuration: dict[str, Any] | None
        try:
            configuration = self._get("/api/v1/system/config")
        except httpx.HTTPError:
            configuration = None
        return ServiceSnapshot(health=health, readiness=readiness, configuration=configuration)
