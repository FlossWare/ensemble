from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Mapping


SIGNATURE_HEADER = "X-Ensemble-Signature"
TIMESTAMP_HEADER = "X-Ensemble-Timestamp"
NONCE_HEADER = "X-Ensemble-Nonce"
PEER_HEADER = "X-Ensemble-Peer"
DEFAULT_CLOCK_SKEW_SECONDS = 300
DEFAULT_NONCE_CAPACITY = 10_000


def canonical_request(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    """Return the unambiguous byte sequence authenticated by a peer signature."""
    digest = hashlib.sha256(body).hexdigest()
    return "\n".join((
        method.upper(),
        path,
        timestamp,
        nonce,
        digest,
    )).encode("utf-8")


def sign_request(secret: str | bytes, method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not key:
        raise ValueError("peer shared secret must not be empty")
    return hmac.new(key, canonical_request(method, path, timestamp, nonce, body), hashlib.sha256).hexdigest()


def signed_headers(
    peer_id: str,
    secret: str | bytes,
    method: str,
    path: str,
    body: bytes = b"",
    *,
    now: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    timestamp = str(int(time.time()) if now is None else int(now))
    request_nonce = nonce or secrets.token_urlsafe(18)
    return {
        PEER_HEADER: peer_id,
        TIMESTAMP_HEADER: timestamp,
        NONCE_HEADER: request_nonce,
        SIGNATURE_HEADER: sign_request(secret, method, path, timestamp, request_nonce, body),
    }


@dataclass(frozen=True)
class Verification:
    accepted: bool
    reason: str


class PeerAuthenticator:
    """Pairwise HMAC authentication with timestamp and in-process replay protection.

    Secrets are configured out of band on both peers. Discovery data is never
    used to establish identity or populate this trust map.
    """

    def __init__(
        self,
        trusted_peers: Mapping[str, str],
        *,
        clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS,
        nonce_capacity: int = DEFAULT_NONCE_CAPACITY,
    ) -> None:
        self._secrets = {peer: secret for peer, secret in trusted_peers.items() if peer and secret}
        self.clock_skew_seconds = max(1, int(clock_skew_seconds))
        self.nonce_capacity = max(1, int(nonce_capacity))
        self._seen: OrderedDict[tuple[str, str], int] = OrderedDict()

    @classmethod
    def from_json(cls, value: str | None) -> "PeerAuthenticator":
        if not value:
            return cls({})
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("ENSEMBLE_FEDERATION_PEER_SECRETS must be a JSON object") from exc
        if not isinstance(parsed, dict) or any(
            not isinstance(peer, str) or not peer.strip()
            or not isinstance(secret, str) or len(secret) < 32
            for peer, secret in parsed.items()
        ):
            raise ValueError("peer secrets must map non-empty instance IDs to secrets of at least 32 characters")
        return cls(parsed)

    def trusted_peer_ids(self) -> list[str]:
        return sorted(self._secrets)

    def verify(
        self,
        *,
        peer_id: str,
        method: str,
        path: str,
        timestamp: str,
        nonce: str,
        signature: str,
        body: bytes,
        now: int | None = None,
    ) -> Verification:
        secret = self._secrets.get(peer_id)
        if secret is None:
            return Verification(False, "peer is not trusted")
        if not nonce or len(nonce) > 128 or not signature:
            return Verification(False, "signature headers are incomplete")
        try:
            issued_at = int(timestamp)
        except (TypeError, ValueError):
            return Verification(False, "timestamp is invalid")
        current = int(time.time()) if now is None else int(now)
        if abs(current - issued_at) > self.clock_skew_seconds:
            return Verification(False, "request timestamp is outside the accepted clock window")
        expected = sign_request(secret, method, path, timestamp, nonce, body)
        if not hmac.compare_digest(expected, signature):
            return Verification(False, "request signature is invalid")
        replay_key = (peer_id, nonce)
        if replay_key in self._seen:
            return Verification(False, "request nonce has already been used")
        self._seen[replay_key] = current
        cutoff = current - self.clock_skew_seconds
        while self._seen:
            _key, seen_at = next(iter(self._seen.items()))
            if seen_at >= cutoff and len(self._seen) <= self.nonce_capacity:
                break
            self._seen.popitem(last=False)
        return Verification(True, "authenticated")
