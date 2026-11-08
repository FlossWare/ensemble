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
