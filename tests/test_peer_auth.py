from ensemble.peer_auth import PeerAuthenticator, sign_request, signed_headers


SECRET = "test-shared-secret-that-is-at-least-32-bytes"


def test_signed_request_authenticates_expected_peer_and_payload():
    body = b'{"task":"search.query"}'
    headers = signed_headers("peer-a", SECRET, "POST", "/federation/tasks", body, now=100, nonce="nonce-1")
    auth = PeerAuthenticator({"peer-a": SECRET})
    result = auth.verify(
        peer_id=headers["X-Ensemble-Peer"],
        method="POST",
        path="/federation/tasks",
        timestamp=headers["X-Ensemble-Timestamp"],
        nonce=headers["X-Ensemble-Nonce"],
        signature=headers["X-Ensemble-Signature"],
        body=body,
        now=100,
    )
    assert result.accepted


def test_signature_binds_method_path_and_body():
    body = b'{"value":1}'
    signature = sign_request(SECRET, "POST", "/federation/tasks", "100", "n", body)
    auth = PeerAuthenticator({"peer-a": SECRET})
    result = auth.verify(
        peer_id="peer-a", method="POST", path="/federation/services",
        timestamp="100", nonce="n", signature=signature, body=body, now=100,
    )
    assert not result.accepted
    assert result.reason == "request signature is invalid"


def test_unknown_peer_and_replayed_nonce_are_rejected():
    headers = signed_headers("peer-a", SECRET, "POST", "/federation/tasks", b"", now=100, nonce="same")
    auth = PeerAuthenticator({"peer-a": SECRET})
    args = dict(
        method="POST", path="/federation/tasks", timestamp=headers["X-Ensemble-Timestamp"],
        nonce=headers["X-Ensemble-Nonce"], signature=headers["X-Ensemble-Signature"], body=b"", now=100,
    )
    assert not auth.verify(peer_id="unknown", **args).accepted
    assert auth.verify(peer_id="peer-a", **args).accepted
    replay = auth.verify(peer_id="peer-a", **args)
    assert not replay.accepted
    assert replay.reason == "request nonce has already been used"


def test_stale_request_and_invalid_secret_configuration_are_rejected():
    headers = signed_headers("peer-a", SECRET, "POST", "/federation/tasks", now=100, nonce="old")
    auth = PeerAuthenticator({"peer-a": SECRET}, clock_skew_seconds=30)
    result = auth.verify(
        peer_id="peer-a", method="POST", path="/federation/tasks",
        timestamp=headers["X-Ensemble-Timestamp"], nonce=headers["X-Ensemble-Nonce"],
        signature=headers["X-Ensemble-Signature"], body=b"", now=200,
    )
    assert not result.accepted
    assert "timestamp" in result.reason
    try:
        PeerAuthenticator.from_json('{"peer-a":"short"}')
    except ValueError as exc:
        assert "32 characters" in str(exc)
    else:
        raise AssertionError("short shared secret must be rejected")
