from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import (
    JSON, BigInteger, Column, DateTime, Integer, MetaData, String, Table,
    and_, insert, select, update,
)
from sqlalchemy.exc import IntegrityError\nfrom sqlalchemy.ext.asyncio import create_async_engine

ledger_metadata = MetaData()
ledger_state = Table(
    "ensemble_ledger_state", ledger_metadata,
    Column("singleton_id", Integer, primary_key=True),
    Column("ledger_id", String(64), nullable=False, unique=True),
    Column("head_sequence", BigInteger, nullable=False),
    Column("head_hash", String(64), nullable=True),
)
ledger_entries = Table(
    "ensemble_ledger_entries", ledger_metadata,
    Column("schema_version", Integer, nullable=False),
    Column("ledger_id", String(64), nullable=False),
    Column("sequence", BigInteger, primary_key=True),
    Column("event_id", String(256), nullable=False, unique=True),
    Column("event_type", String(256), nullable=False),
    Column("source", String(512), nullable=False),
    Column("timestamp", String(64), nullable=True),
    Column("captured_at", String(64), nullable=False),
    Column("correlation_id", String(256), nullable=True),
    Column("payload", JSON, nullable=False),
    Column("payload_sha256", String(64), nullable=False),
    Column("previous_entry_sha256", String(64), nullable=True),
    Column("entry_sha256", String(64), nullable=False, unique=True),
    Column("provenance", JSON, nullable=False),
)
Index = __import__("sqlalchemy").Index
Index("ix_ensemble_ledger_event_type_sequence", ledger_entries.c.event_type, ledger_entries.c.sequence)
Index("ix_ensemble_ledger_source_sequence", ledger_entries.c.source, ledger_entries.c.sequence)
Index("ix_ensemble_ledger_correlation_sequence", ledger_entries.c.correlation_id, ledger_entries.c.sequence)


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("ledger values must be canonical JSON") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def entry_hash(envelope: dict[str, Any]) -> str:
    return digest({key: value for key, value in envelope.items() if key != "entry_sha256"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamps must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_event(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("entry must be an object")
    event_id = value.get("event_id") or str(uuid.uuid4())
    event_type, source = value.get("event_type"), value.get("source")
    payload = value.get("payload")
    if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 256:
        raise ValueError("event_id must be 1-256 characters")
    if not isinstance(event_type, str) or not event_type.strip() or len(event_type) > 256:
        raise ValueError("event_type must be 1-256 characters")
    if not isinstance(source, str) or not source.strip() or len(source) > 512:
        raise ValueError("source must be 1-512 characters")
    if not isinstance(payload, (dict, list, str, int, float, bool)) and payload is not None:
        raise ValueError("payload must be JSON")
    canonical(payload)
    timestamp = value.get("timestamp")
    if timestamp is not None:
        parse_time(timestamp)
    correlation_id = value.get("correlation_id")
    if correlation_id is not None and (not isinstance(correlation_id, str) or len(correlation_id) > 256):
        raise ValueError("correlation_id must be at most 256 characters")
    return {
        "event_id": event_id, "event_type": event_type, "source": source,
        "timestamp": timestamp, "correlation_id": correlation_id, "payload": payload,
    }


def envelope_for(ledger_id: str, sequence: int, event: dict[str, Any], captured_at: str,
                 previous_hash: str | None, provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    record = {
        "schema_version": 1, "ledger_id": ledger_id, "sequence": sequence,
        **event, "captured_at": captured_at, "payload_sha256": digest(event["payload"]),
        "previous_entry_sha256": previous_hash, "provenance": provenance or {},
    }
    record["entry_sha256"] = entry_hash(record)
    return record


class SqliteLedgerStore:
    """Append-only ledger using the application's SQLite engine and transaction boundary."""
    def __init__(self, engine):
        self.engine = engine

    async def initialize(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(ledger_metadata.create_all)
            row = (await conn.execute(select(ledger_state.c.ledger_id).where(ledger_state.c.singleton_id == 1))).first()
            if row is None:
                await conn.execute(insert(ledger_state).values(
                    singleton_id=1, ledger_id=str(uuid.uuid4()), head_sequence=0, head_hash=None
                ))

    async def status(self):
        async with self.engine.connect() as conn:
            state = (await conn.execute(select(ledger_state).where(ledger_state.c.singleton_id == 1))).mappings().one()
            return {"ledger_id": state["ledger_id"], "head_sequence": state["head_sequence"], "head_hash": state["head_hash"]}

    async def append(self, value):
        event = validate_event(value)
        try:
            async with self.engine.begin() as conn:
                state = (await conn.execute(select(ledger_state).where(ledger_state.c.singleton_id == 1))).mappings().one()
                existing = (await conn.execute(select(ledger_entries).where(ledger_entries.c.event_id == event["event_id"]))).mappings().first()
                if existing:
                    if digest(existing["payload"]) == digest(event["payload"]) and all(existing[k] == event[k] for k in ("event_type", "source", "timestamp", "correlation_id")):
                        return {"status": "duplicate", "entry": dict(existing)}
                    raise ValueError("event_id already exists with different content")
                allocated = (await conn.execute(
                    update(ledger_state).where(ledger_state.c.singleton_id == 1)
                    .values(head_sequence=ledger_state.c.head_sequence + 1)
                    .returning(ledger_state.c.ledger_id, ledger_state.c.head_sequence, ledger_state.c.head_hash)
                )).mappings().one()
                raced = (await conn.execute(select(ledger_entries).where(ledger_entries.c.event_id == event["event_id"]))).mappings().first()
                if raced:
                    raise RuntimeError("ledger duplicate append raced")
                seq = allocated["head_sequence"]
                record = envelope_for(allocated["ledger_id"], seq, event, utc_now(), allocated["head_hash"])
                await conn.execute(insert(ledger_entries).values(**record))
                await conn.execute(update(ledger_state).where(ledger_state.c.singleton_id == 1).values(head_hash=record["entry_sha256"]))
                return {"status": "stored", "entry": record}
        except RuntimeError as exc:
            if str(exc) != "ledger duplicate append raced":
                raise
            existing = await self.get(event["event_id"])
            if existing and digest(existing["payload"]) == digest(event["payload"]) and all(existing[k] == event[k] for k in ("event_type", "source", "timestamp", "correlation_id")):
                return {"status": "duplicate", "entry": existing}
            raise ValueError("event_id already exists with different content") from exc

    async def pull(self, after_sequence=0, through_sequence=None, limit=100, **filters):
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        state = await self.status()
        head = state["head_sequence"]
        if after_sequence > head:
            raise ValueError("after_sequence exceeds head_sequence")
        upper = head if through_sequence is None else through_sequence
        if isinstance(upper, bool) or not isinstance(upper, int) or upper < 0 or upper > head:
            raise ValueError("through_sequence must be between 0 and head_sequence")
        since, until = parse_time(filters.get("since")), parse_time(filters.get("until"))
        if since and until and since > until:
            raise ValueError("since must not be later than until")
        clauses = [ledger_entries.c.sequence > after_sequence, ledger_entries.c.sequence <= upper]
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(ledger_entries).where(and_(*clauses))
                .order_by(ledger_entries.c.sequence).limit(limit))).mappings().all()
            entries = []
            for row in rows:
                item = dict(row)
                if filters.get("event_id") and item["event_id"] != filters["event_id"]: continue
                if filters.get("event_type") and item["event_type"] != filters["event_type"]: continue
                if filters.get("source") and item["source"] != filters["source"]: continue
                if filters.get("correlation_id") and item["correlation_id"] != filters["correlation_id"]: continue
                stamp = parse_time(item["timestamp"]) if item["timestamp"] else None
                if since and (stamp is None or stamp < since): continue
                if until and (stamp is None or stamp > until): continue
                entries.append(item)
        scanned = rows[-1]["sequence"] if rows else after_sequence
        has_more = scanned < upper
        return {"entries": entries, "next_after_sequence": scanned, "head_sequence": head, "has_more": has_more}

    async def get(self, event_id):
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(ledger_entries).where(ledger_entries.c.event_id == event_id))).mappings().first()
            return dict(row) if row else None

    async def verify(self, from_sequence=1, through_sequence=None):
        state = await self.status()
        end = state["head_sequence"] if through_sequence is None else through_sequence
        if not 1 <= from_sequence <= max(1, end) or end > state["head_sequence"]:
            raise ValueError("invalid verification range")
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(ledger_entries).where(
                and_(ledger_entries.c.sequence >= from_sequence, ledger_entries.c.sequence <= end)
            ).order_by(ledger_entries.c.sequence))).mappings().all()
        expected = from_sequence
        previous = None
        if from_sequence > 1:
            # A local predecessor is not a trusted checkpoint: it can be tampered too.
            return {"ok": False, "first_failing_sequence": from_sequence, "failure_category": "missing_checkpoint", "assurance": "unverified_partial_range", "verified_count": 0}
        seen = set()
        for row in rows:
            item = dict(row)
            seq = item["sequence"]
            if seq != expected: return {"ok": False, "first_failing_sequence": expected, "failure_category": "sequence_gap", "assurance": "hash_chain", "verified_count": expected-from_sequence}
            if item["event_id"] in seen: return {"ok": False, "first_failing_sequence": seq, "failure_category": "duplicate_event_id", "assurance": "hash_chain", "verified_count": seq-from_sequence}
            seen.add(item["event_id"])
            if not hmac.compare_digest(item["payload_sha256"], digest(item["payload"])):
                return {"ok": False, "first_failing_sequence": seq, "failure_category": "payload_hash_mismatch", "assurance": "hash_chain", "verified_count": seq-from_sequence}
            if item["previous_entry_sha256"] != previous:
                return {"ok": False, "first_failing_sequence": seq, "failure_category": "broken_hash_chain", "assurance": "hash_chain", "verified_count": seq-from_sequence}
            if not hmac.compare_digest(item["entry_sha256"], entry_hash(item)):
                return {"ok": False, "first_failing_sequence": seq, "failure_category": "entry_hash_mismatch", "assurance": "hash_chain", "verified_count": seq-from_sequence}
            previous, expected = item["entry_sha256"], expected + 1
        if expected <= end:
            return {"ok": False, "first_failing_sequence": expected, "failure_category": "missing_sequence", "assurance": "hash_chain", "verified_count": expected-from_sequence}
        return {"ok": True, "from_sequence": from_sequence, "through_sequence": end, "verified_count": len(rows), "assurance": "hash_chain" if from_sequence == 1 else "hash_chain_with_local_predecessor"}

    async def import_batch(self, source_ledger_id, entries):
        if not isinstance(source_ledger_id, str) or not source_ledger_id or not isinstance(entries, list) or len(entries) > 1000:
            raise ValueError("source_ledger_id and up to 1000 entries are required")
        imported, duplicates = [], []
        async with self.engine.begin() as conn:
            state = (await conn.execute(select(ledger_state).where(ledger_state.c.singleton_id == 1))).mappings().one()
            seq, previous = state["head_sequence"], state["head_hash"]
            for incoming in entries:
                event = validate_event(incoming)
                if incoming.get("payload_sha256") != digest(event["payload"]):
                    raise ValueError("import payload hash mismatch")
                existing = (await conn.execute(select(ledger_entries).where(ledger_entries.c.event_id == event["event_id"]))).mappings().first()
                if existing:
                    if digest(existing["payload"]) == digest(event["payload"]) and existing["event_type"] == event["event_type"] and existing["source"] == event["source"]:
                        duplicates.append(event["event_id"]); continue
                    raise ValueError("import conflict: event_id already exists with different content")
                seq += 1
                provenance = {"source_ledger_id": source_ledger_id, "source_sequence": incoming.get("sequence"), "source_entry_sha256": incoming.get("entry_sha256"), "source_provenance": incoming.get("provenance", {})}
                record = envelope_for(state["ledger_id"], seq, event, incoming.get("captured_at") or utc_now(), previous, provenance)
                await conn.execute(insert(ledger_entries).values(**record))
                previous = record["entry_sha256"]
                imported.append(event["event_id"])
            await conn.execute(update(ledger_state).where(ledger_state.c.singleton_id == 1).values(head_sequence=seq, head_hash=previous))
        return {"imported": imported, "duplicates": duplicates, "head_sequence": seq}


router = APIRouter(tags=["ledger"])


def _store(request: Request):
    store = getattr(request.app.state, "ledger_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="ledger unavailable")
    return store


@router.post("/api/v1/ledger/entries", status_code=201)
async def ledger_append(payload: dict[str, Any], request: Request):
    try: return {"ok": True, **(await _store(request).append(payload))}
    except ValueError as exc: raise HTTPException(status_code=409 if "exists" in str(exc) else 400, detail=str(exc)) from exc


@router.get("/api/v1/ledger/entries")
async def ledger_pull(request: Request, after_sequence: int = 0, through_sequence: int | None = None,
                      limit: int = Query(100, ge=1, le=1000), event_id: str | None = None,
                      event_type: str | None = None, source: str | None = None,
                      correlation_id: str | None = None, since: str | None = None, until: str | None = None):
    try: return {"ok": True, **(await _store(request).pull(after_sequence, through_sequence, limit,
        event_id=event_id, event_type=event_type, source=source, correlation_id=correlation_id, since=since, until=until))}
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/v1/ledger/entries/{event_id}")
async def ledger_get(event_id: str, request: Request):
    item = await _store(request).get(event_id)
    if item is None: raise HTTPException(status_code=404, detail="ledger event not found")
    return {"ok": True, "entry": item}


@router.get("/api/v1/ledger/status")
async def ledger_status(request: Request):
    return {"ok": True, **(await _store(request).status())}


@router.post("/api/v1/ledger/verify")
async def ledger_verify(payload: dict[str, Any], request: Request):
    try: return {"ok": True, **(await _store(request).verify(payload.get("from_sequence", 1), payload.get("through_sequence")))}
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/v1/ledger/import")
async def ledger_import(payload: dict[str, Any], request: Request):
    try: return {"ok": True, **(await _store(request).import_batch(payload.get("source_ledger_id"), payload.get("entries")))}
    except ValueError as exc: raise HTTPException(status_code=409 if "conflict" in str(exc) else 400, detail=str(exc)) from exc
