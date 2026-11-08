from ensemble.federation import PeerRegistry


def _announcement(instance_id="peer-1"):
    return {
        "kind": "ensemble.presence",
        "protocol_version": "1",
        "instance_id": instance_id,
        "name": "server-01",
        "endpoint": "http://192.168.1.20:8090",
        "capabilities": ["search", "embeddings", "search"],
    }


def test_registry_tracks_peer_and_deduplicates_capabilities():
    registry = PeerRegistry("local", ttl_seconds=30)
    assert registry.observe(_announcement(), now=100)
    peer = registry.list(now=101)[0]
    assert peer["instance_id"] == "peer-1"
    assert peer["status"] == "online"
    assert peer["capabilities"] == ["embeddings", "search"]
    assert peer["announcements"] == 1

    assert registry.observe(_announcement(), now=110)
    peer = registry.list(now=111)[0]
    assert peer["announcements"] == 2
    assert peer["last_seen"] == 110
    assert peer["expires_at"] == 140


def test_registry_ignores_self_and_invalid_announcements():
    registry = PeerRegistry("local")
    assert not registry.observe(_announcement("local"), now=100)
    assert not registry.observe({"instance_id": "missing-fields"}, now=100)
    assert not registry.observe({**_announcement(), "endpoint": "file:///etc/passwd"}, now=100)
    assert registry.list(now=100) == []


def test_registry_marks_expired_peer_offline_then_eventually_prunes_it():
    registry = PeerRegistry("local", ttl_seconds=30)
    assert registry.observe(_announcement(), now=100)
    peer = registry.list(now=131)[0]
    assert peer["status"] == "offline"
    assert registry.expire(now=131) == 0
    assert len(registry.list(now=131)) == 1
    assert registry.expire(now=221) == 1
    assert registry.list(now=221) == []



def test_graceful_departure_marks_peer_offline():
    registry = PeerRegistry("local", ttl_seconds=30)
    assert registry.observe(_announcement(), now=100)
    assert registry.depart("peer-1", now=105)
    peer = registry.list(now=106)[0]
    assert peer["status"] == "offline"
    assert peer["seconds_since_seen"] == 6



def test_inspect_peer_reports_remote_readiness(monkeypatch):
    import asyncio
    from ensemble import federation

    def fake_read(endpoint, path, timeout):
        if path == "/health":
            return {"reachable": True, "http_status": 200, "data": {"status": "ok"}}
        return {"reachable": True, "http_status": 200, "data": {"status": "ready", "capabilities": ["search", "memory"]}}

    monkeypatch.setattr(federation, "_read_peer_endpoint", fake_read)
    peer = _announcement()
    peer["last_seen"] = 100
    peer["expires_at"] = 200
    peer["first_seen"] = 90
    result = asyncio.run(federation.inspect_peer(peer))
    assert result["remote_status"] == "ready"
    assert result["capabilities"] == ["search", "memory"]
    assert result["health"]["data"]["status"] == "ok"


def test_inspect_peer_reports_unreachable_peer(monkeypatch):
    import asyncio
    from ensemble import federation

    def fake_read(endpoint, path, timeout):
        return {"reachable": False, "error": "timeout"}

    monkeypatch.setattr(federation, "_read_peer_endpoint", fake_read)
    peer = _announcement()
    result = asyncio.run(federation.inspect_peer(peer))
    assert result["remote_status"] == "unreachable"
    assert result["capabilities"] == peer["capabilities"]
