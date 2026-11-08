from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


DEFAULT_MULTICAST_GROUP = "239.255.76.76"
DEFAULT_MULTICAST_PORT = 47676
PROTOCOL_VERSION = "1"


def _instance_id(root: Path) -> str:
    path = root / "instance-id"
    try:
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    except FileNotFoundError:
        pass
    value = str(uuid4())
    root.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(value + "\n", encoding="utf-8")
    try:
        temporary.replace(path)
    except OSError:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        raise
    return value


def _advertise_host(bind_host: str) -> str:
    if bind_host not in {"0.0.0.0", "::", ""}:
        return bind_host
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        probe.close()


@dataclass
class Peer:
    instance_id: str
    name: str
    endpoint: str
    protocol_version: str
    capabilities: list[str]
    first_seen: float
    last_seen: float
    expires_at: float
    announcements: int = 1

    def as_dict(self, now: float | None = None) -> dict[str, Any]:
        current = time.time() if now is None else now
        return {
            "instance_id": self.instance_id,
            "name": self.name,
            "endpoint": self.endpoint,
            "protocol_version": self.protocol_version,
            "capabilities": self.capabilities,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "expires_at": self.expires_at,
            "seconds_since_seen": max(0, round(current - self.last_seen, 1)),
            "announcements": self.announcements,
            "status": "online" if self.expires_at > current else "offline",
        }


class PeerRegistry:
    """In-memory view of peers heard through federation discovery."""

    def __init__(self, instance_id: str, ttl_seconds: int = 90) -> None:
        self.instance_id = instance_id
        self.ttl_seconds = max(10, int(ttl_seconds))
        self._peers: dict[str, Peer] = {}

    def observe(self, message: dict[str, Any], now: float | None = None) -> bool:
        current = time.time() if now is None else now
        instance_id = message.get("instance_id")
        endpoint = message.get("endpoint")
        name = message.get("name")
        if not all(isinstance(value, str) and value.strip() for value in (instance_id, endpoint, name)):
            return False
        if instance_id == self.instance_id:
            return False
        if len(instance_id) > 128 or len(endpoint) > 512 or len(name) > 128:
            return False
        if not endpoint.startswith(("http://", "https://")):
            return False
        capabilities = message.get("capabilities", [])
        if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
            return False
        peer = self._peers.get(instance_id)
        if peer is None:
            self._peers[instance_id] = Peer(
                instance_id=instance_id,
                name=name,
                endpoint=endpoint.rstrip("/"),
                protocol_version=str(message.get("protocol_version", PROTOCOL_VERSION)),
                capabilities=sorted(set(capabilities)),
                first_seen=current,
                last_seen=current,
                expires_at=current + self.ttl_seconds,
            )
        else:
            peer.name = name
            peer.endpoint = endpoint.rstrip("/")
            peer.protocol_version = str(message.get("protocol_version", PROTOCOL_VERSION))
            peer.capabilities = sorted(set(capabilities))
            peer.last_seen = current
            peer.expires_at = current + self.ttl_seconds
            peer.announcements += 1
        return True

    def list(self, now: float | None = None) -> list[dict[str, Any]]:
        current = time.time() if now is None else now
        return [
            self._peers[key].as_dict(current)
            for key in sorted(self._peers)
        ]

    def depart(self, instance_id: str, now: float | None = None) -> bool:
        peer = self._peers.get(instance_id)
        if peer is None:
            return False
        peer.expires_at = time.time() if now is None else now
        return True

    def expire(self, now: float | None = None) -> int:
        current = time.time() if now is None else now
        expired = [key for key, peer in self._peers.items() if peer.expires_at + (self.ttl_seconds * 3) <= current]
        for key in expired:
            del self._peers[key]
        return len(expired)


class MulticastDiscovery:
    """Best-effort LAN discovery. Durable work never travels over UDP."""

    def __init__(
        self,
        registry: PeerRegistry,
        *,
        instance_id: str,
        name: str,
        endpoint: str,
        capabilities: list[str],
        group: str = DEFAULT_MULTICAST_GROUP,
        port: int = DEFAULT_MULTICAST_PORT,
        heartbeat_seconds: int = 30,
        ttl_seconds: int = 90,
    ) -> None:
        self.registry = registry
        self.message = {
            "kind": "ensemble.presence",
            "protocol_version": PROTOCOL_VERSION,
            "instance_id": instance_id,
            "name": name,
            "endpoint": endpoint.rstrip("/"),
            "capabilities": sorted(set(capabilities)),
        }
        self.group = group
        self.port = int(port)
        self.heartbeat_seconds = max(5, int(heartbeat_seconds))
        self.ttl_seconds = max(self.heartbeat_seconds * 2, int(ttl_seconds))
        self._socket: socket.socket | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._started = False
        self.last_error: str | None = None

    async def start(self) -> None:
        if self._started:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_ADD_MEMBERSHIP,
                socket.inet_aton(self.group) + socket.inet_aton("0.0.0.0"),
            )
            sock.bind(("", self.port))
            sock.setblocking(False)
        except OSError as exc:
            sock.close()
            self.last_error = f"{type(exc).__name__}: {exc}"
            return
        self._socket = sock
        self._started = True
        self._tasks = [
            asyncio.create_task(self._receive_loop(), name="ensemble-federation-receive"),
            asyncio.create_task(self._announce_loop(), name="ensemble-federation-announce"),
        ]

    async def stop(self) -> None:
        if not self._started:
            return
        if self._socket is not None:
            departure = {**self.message, "kind": "ensemble.departure"}
            try:
                await asyncio.get_running_loop().sock_sendto(
                    self._socket,
                    json.dumps(departure, separators=(",", ":")).encode("utf-8"),
                    (self.group, self.port),
                )
            except OSError:
                pass
        self._started = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    async def _announce_loop(self) -> None:
        assert self._socket is not None
        loop = asyncio.get_running_loop()
        while self._started:
            try:
                payload = json.dumps(self.message, separators=(",", ":")).encode("utf-8")
                await loop.sock_sendto(self._socket, payload, (self.group, self.port))
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except OSError as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
            self.registry.expire()
            await asyncio.sleep(self.heartbeat_seconds)

    async def _receive_loop(self) -> None:
        assert self._socket is not None
        loop = asyncio.get_running_loop()
        while self._started:
            try:
                payload, _address = await loop.sock_recvfrom(self._socket, 65535)
                message = json.loads(payload.decode("utf-8"))
                if isinstance(message, dict) and message.get("kind") == "ensemble.presence":
                    self.registry.observe(message)
                elif isinstance(message, dict) and message.get("kind") == "ensemble.departure":
                    instance_id = message.get("instance_id")
                    if isinstance(instance_id, str):
                        self.registry.depart(instance_id)
            except asyncio.CancelledError:
                raise
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue



def _read_peer_endpoint(endpoint: str, path: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint.rstrip("/") + path,
        headers={"Accept": "application/json", "User-Agent": "Ensemble-Federation/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(1024 * 1024)
            data = json.loads(payload.decode("utf-8"))
            return {"reachable": True, "http_status": response.status, "data": data}
    except (OSError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}


def host_metrics(persistence_root: str | Path) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "cpu_count": os.cpu_count(),
        "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
    }
    try:
        memory: dict[str, int] = {}
        with open("/proc/meminfo", encoding="ascii") as stream:
            for line in stream:
                key, value = line.split(":", 1)
                if key in {"MemTotal", "MemAvailable"}:
                    memory[key] = int(value.strip().split()[0]) * 1024
        if "MemTotal" in memory:
            metrics["memory"] = {
                "total_bytes": memory["MemTotal"],
                "available_bytes": memory.get("MemAvailable"),
                "used_bytes": memory["MemTotal"] - memory.get("MemAvailable", memory["MemTotal"]),
            }
    except (OSError, ValueError, IndexError):
        metrics["memory"] = None
    try:
        disk = shutil.disk_usage(persistence_root)
        metrics["persistence_disk"] = {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
        }
    except OSError:
        metrics["persistence_disk"] = None
    return metrics


async def inspect_peer(peer: dict[str, Any], timeout: float = 2.0) -> dict[str, Any]:
    """Fetch live operational details from a discovered peer without blocking the event loop."""
    endpoint = peer.get("endpoint", "")
    if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
        return {**peer, "health": {"reachable": False, "error": "invalid endpoint"}}
    health, ready, metrics = await asyncio.gather(
        asyncio.to_thread(_read_peer_endpoint, endpoint, "/health", timeout),
        asyncio.to_thread(_read_peer_endpoint, endpoint, "/ready", timeout),
        asyncio.to_thread(_read_peer_endpoint, endpoint, "/federation/metrics", timeout),
    )
    return {
        **peer,
        "health": health,
        "readiness": ready,
        "metrics": metrics.get("data") if metrics.get("reachable") else None,
        "remote_status": (
            "ready" if health.get("reachable") and ready.get("reachable")
            and ready.get("data", {}).get("status") == "ready"
            else "degraded" if health.get("reachable") else "unreachable"
        ),
        "capabilities": (
            ready.get("data", {}).get("capabilities", peer.get("capabilities", []))
            if ready.get("reachable") else peer.get("capabilities", [])
        ),
    }
