from __future__ import annotations

from typing import Any

import httpx

from ensemble.terminal_client import ControlCenterClient


def test_snapshot_reads_health_readiness_and_configuration(monkeypatch: Any) -> None:
    payloads = {
        "/health": {"status": "ok"},
        "/ready": {"status": "ready", "capabilities": ["memory", "graph"]},
        "/api/v1/system/config": {"host": "127.0.0.1", "port": 8090, "database": "sqlite"},
    }
    requested: list[str] = []

    def fake_get(url: str, timeout: float) -> httpx.Response:
        path = url.removeprefix("http://ensemble.test")
        requested.append(path)
        request = httpx.Request("GET", url)
        return httpx.Response(200, json=payloads[path], request=request)

    monkeypatch.setattr(httpx, "get", fake_get)
    snapshot = ControlCenterClient("http://ensemble.test").snapshot()

    assert snapshot.health == payloads["/health"]
    assert snapshot.readiness == payloads["/ready"]
    assert snapshot.configuration == payloads["/api/v1/system/config"]
    assert requested == ["/health", "/ready", "/api/v1/system/config"]


def test_snapshot_keeps_configuration_optional_when_endpoint_unavailable(monkeypatch: Any) -> None:
    def fake_get(url: str, timeout: float) -> httpx.Response:
        request = httpx.Request("GET", url)
        if url.endswith("/api/v1/system/config"):
            return httpx.Response(404, request=request)
        payload = {"status": "ok"} if url.endswith("/health") else {"status": "ready"}
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(httpx, "get", fake_get)
    snapshot = ControlCenterClient("http://ensemble.test").snapshot()

    assert snapshot.health["status"] == "ok"
    assert snapshot.readiness["status"] == "ready"
    assert snapshot.configuration is None


def test_snapshot_rejects_non_object_payload(monkeypatch: Any) -> None:
    def fake_get(url: str, timeout: float) -> httpx.Response:
        return httpx.Response(200, json=[], request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    try:
        ControlCenterClient("http://ensemble.test").snapshot()
    except ValueError as exc:
        assert "Expected an object response" in str(exc)
    else:
        raise AssertionError("non-object API response must be rejected")
