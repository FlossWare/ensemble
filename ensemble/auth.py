from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    subject: str
    partitions: frozenset[str]
    permissions: frozenset[str]


def load_api_keys() -> dict[str, Principal]:
    raw = os.getenv("ENSEMBLE_API_KEYS", "").strip()
    if not raw:
        return {}
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ENSEMBLE_API_KEYS must contain a valid JSON object") from exc
    if not isinstance(values, dict):
        raise RuntimeError("ENSEMBLE_API_KEYS must be a JSON object keyed by API key")
    result: dict[str, Principal] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key or not isinstance(value, dict):
            raise RuntimeError("Each API key entry must map a non-empty key to an object")
        subject = str(value.get("subject", "")).strip()
        partitions = value.get("partitions", [])
        permissions = value.get("permissions", [])
        if not subject or not isinstance(partitions, list) or not partitions:
            raise RuntimeError("Each API key needs a subject and at least one partition")
        if not isinstance(permissions, list) or not permissions:
            raise RuntimeError("Each API key needs at least one permission")
        if not set(permissions).issubset({"read", "write", "admin"}):
            raise RuntimeError("API key permissions must be read, write, or admin")
        result[key] = Principal(subject, frozenset(map(str, partitions)), frozenset(permissions))
    return result


def authenticate(token: str, api_keys: dict[str, Principal]) -> Principal | None:
    match: Principal | None = None
    for candidate, principal in api_keys.items():
        if hmac.compare_digest(token, candidate):
            match = principal
    return match
