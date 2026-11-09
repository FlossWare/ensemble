#!/usr/bin/env python3
"""Bidirectional, non-destructive HTTP transfer test for live POC and Ensemble APIs."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request


def request(base: str, method: str, path: str, payload=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} returned HTTP {exc.code}: {detail}") from exc


def transfer(source: str, destination: str, label: str) -> int:
    before = request(source, "GET", "/api/v1/ledger/status")
    ledger_id = before["ledger_id"]
    head = before["head_sequence"]
    cursor = 0
    transferred = 0
    while cursor < head:
        query = urllib.parse.urlencode({
            "after_sequence": cursor, "through_sequence": head, "limit": 1000,
        })
        page = request(source, "GET", "/api/v1/ledger/entries?" + query)
        entries = page["entries"]
        if entries:
            result = request(destination, "POST", "/api/v1/ledger/import", {
                "source_ledger_id": ledger_id, "entries": entries,
            })
            if not result.get("ok"):
                raise RuntimeError(f"{label} import failed: {result}")
            transferred += len(result.get("imported", []))
        next_cursor = page["next_after_sequence"]
        if next_cursor <= cursor and page["has_more"]:
            raise RuntimeError(f"{label} source cursor did not advance")
        cursor = next_cursor
        if not page["has_more"]:
            break
    after = request(source, "GET", "/api/v1/ledger/status")
    if (after["ledger_id"], after["head_sequence"], after.get("head_hash")) != (
        before["ledger_id"], before["head_sequence"], before.get("head_hash")
    ):
        raise RuntimeError(f"{label} source ledger changed during read-only transfer")
    return transferred


def main() -> int:
    poc = os.environ.get("POC_LEDGER_URL")
    ensemble = os.environ.get("ENSEMBLE_LEDGER_URL")
    if not poc or not ensemble:
        raise SystemExit("Set POC_LEDGER_URL and ENSEMBLE_LEDGER_URL to live API base URLs")
    poc_to_ensemble = transfer(poc, ensemble, "POC-to-Ensemble")
    ensemble_to_poc = transfer(ensemble, poc, "Ensemble-to-POC")
    print(json.dumps({
        "ok": True,
        "poc_to_ensemble_imported": poc_to_ensemble,
        "ensemble_to_poc_imported": ensemble_to_poc,
        "source_reads_non_destructive": True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
