from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("ledger values must be canonical JSON") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def envelope_hash(value: dict[str, Any]) -> str:
    return digest({key: item for key, item in value.items() if key != "entry_sha256"})

def validate_envelope(record: dict[str, Any], ledger_id: str) -> None:
    required = {"schema_version", "ledger_id", "sequence", "event_id", "event_type", "source",
                "timestamp", "captured_at", "correlation_id", "payload", "payload_sha256",
                "previous_entry_sha256", "entry_sha256", "provenance"}
    if not isinstance(record, dict) or not required.issubset(record):
        raise ValueError("invalid ledger envelope: required field missing")
    if isinstance(record["schema_version"], bool) or record["schema_version"] != 1 or isinstance(record["sequence"], bool) or not isinstance(record["sequence"], int) or record["sequence"] < 1:
        raise ValueError("invalid ledger envelope version or sequence")
    if record["ledger_id"] != ledger_id:
        raise ValueError("ledger envelope identity mismatch")
    for field, maximum in (("event_id", 256), ("event_type", 256), ("source", 512)):
        value = record[field]
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError("invalid ledger envelope field: " + field)
    if record["timestamp"] is not None:
        parse_time(record["timestamp"])
    parse_time(record["captured_at"])
    if record["correlation_id"] is not None and not isinstance(record["correlation_id"], str):
        raise ValueError("invalid ledger envelope correlation_id")
    if not isinstance(record["provenance"], dict):
        raise ValueError("invalid ledger envelope provenance")
    for field in ("payload_sha256", "entry_sha256"):
        value = record[field]
        if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("invalid ledger envelope hash field: " + field)
    previous_hash = record["previous_entry_sha256"]
    if previous_hash is not None and (not isinstance(previous_hash, str) or len(previous_hash) != 64 or any(char not in "0123456789abcdef" for char in previous_hash)):
        raise ValueError("invalid ledger envelope previous hash")
    if record["payload_sha256"] != digest(record["payload"]):
        raise ValueError("payload hash mismatch")
    if not hmac.compare_digest(record["entry_sha256"], envelope_hash(record)):
        raise ValueError("entry hash mismatch")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

class LedgerImportError(RuntimeError):
    def __init__(self, message: str, committed_prefix: int, retry_after_source_sequence: int | None):
        super().__init__(message)
        self.committed_prefix = committed_prefix
        self.retry_after_source_sequence = retry_after_source_sequence



def parse_time(value: str | None):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("timestamps must be ISO-8601 strings")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamps must be ISO-8601") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return result.astimezone(timezone.utc)


def validate_event(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("entry must be an object")
    event_id = value.get("event_id", str(uuid.uuid4()))
    event_type = value.get("event_type", value.get("kind"))
    source = value.get("source", value.get("producer"))
    payload = value.get("payload")
    if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 256:
        raise ValueError("event_id must be 1-256 characters")
    if not isinstance(event_type, str) or not event_type.strip() or len(event_type) > 256:
        raise ValueError("event_type must be 1-256 characters")
    if not isinstance(source, str) or not source.strip() or len(source) > 512:
        raise ValueError("source must be 1-512 characters")
    canonical(payload)
    timestamp = value.get("timestamp")
    if timestamp is not None:
        parse_time(timestamp)
    correlation_id = value.get("correlation_id")
    if correlation_id is not None and (not isinstance(correlation_id, str) or len(correlation_id) > 256):
        raise ValueError("correlation_id must be at most 256 characters")
    return {"event_id": event_id, "event_type": event_type, "source": source,
            "timestamp": timestamp, "correlation_id": correlation_id, "payload": payload}


class JsonlAppendOnlyLedger:
    """Filesystem-backed, non-destructive append-only sequence ledger."""
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.meta_path = self.path.with_suffix(self.path.suffix + ".ledger-meta.json")
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_id = self._load_ledger_id()

    def _load_ledger_id(self) -> str:
        with self._locked():
            if self.meta_path.exists():
                value = json.loads(self.meta_path.read_text(encoding="utf-8"))
                if isinstance(value, dict) and isinstance(value.get("ledger_id"), str) and value["ledger_id"]:
                    return value["ledger_id"]
                raise RuntimeError("ledger metadata file is invalid")
            ledger_id = str(uuid.uuid4())
            temp = self.meta_path.with_name(self.meta_path.name + "." + uuid.uuid4().hex + ".tmp")
            with temp.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps({"ledger_id": ledger_id}, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.meta_path)
            return ledger_id

    @contextmanager
    def _locked(self, shared: bool = False):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _scan(self) -> Iterator[tuple[int, dict[str, Any], bytes]]:
        if not self.path.exists():
            return
        with self.path.open("rb") as handle:
            sequence = 0
            for line_number, raw in enumerate(handle, 1):
                if raw and not raw.endswith(b"\n"):
                    raise RuntimeError(f"session ledger unterminated final record at line {line_number}")
                if not raw.strip():
                    continue
                sequence += 1
                try:
                    record = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(f"session ledger malformed JSON at line {line_number}") from exc
                if not isinstance(record, dict):
                    raise RuntimeError(f"session ledger record at line {line_number} is not an object")
                declared_sequence = record.get("sequence", sequence)
                if isinstance(declared_sequence, bool) or not isinstance(declared_sequence, int) or declared_sequence < 1:
                    declared_sequence = sequence
                yield declared_sequence, record, raw

    def _head(self):
        sequence, last_hash, legacy_digest, legacy_count = 0, None, hashlib.sha256(b"").hexdigest(), 0
        for sequence, record, raw in self._scan():
            if record.get("entry_sha256"):
                last_hash = record["entry_sha256"]
            else:
                if "schema_version" in record or "sequence" in record or last_hash is not None:
                    raise RuntimeError(f"ledger envelope is incomplete or legacy data follows hashed data at sequence {sequence}")
                legacy_count += 1
                legacy_digest = hashlib.sha256(bytes.fromhex(legacy_digest) + raw.rstrip(b"\r\n")).hexdigest()
        return sequence, last_hash, legacy_digest, legacy_count

    def _status_unlocked(self):
        head, last_hash, legacy_digest, legacy_count = self._head()
        return {"ledger_id": self.ledger_id, "head_sequence": head, "head_hash": last_hash,
                "legacy_prefix_sequence_count": legacy_count,
                "legacy_prefix_sha256": legacy_digest if legacy_count else None}

    def status(self):
        with self._locked(shared=True):
            return self._status_unlocked()

    def _public_entry(self, sequence: int, record: dict[str, Any], raw: bytes) -> dict[str, Any]:
        if "schema_version" in record:
            return record
        original = dict(record)
        event_id = record.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            event_id = "legacy-" + hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest()
        event_type = record.get("event_type", record.get("kind", "legacy.unknown"))
        source = record.get("source", record.get("producer", "legacy"))
        payload = record.get("payload", original)
        timestamp = record.get("timestamp")
        captured_at = record.get("captured_at") or timestamp or "1970-01-01T00:00:00Z"
        return {
            "schema_version": 0, "ledger_id": self.ledger_id, "sequence": sequence,
            "event_id": event_id, "event_type": event_type, "source": source,
            "timestamp": timestamp, "captured_at": captured_at,
            "correlation_id": record.get("correlation_id"), "payload": payload,
            "payload_sha256": digest(payload), "previous_entry_sha256": None,
            "entry_sha256": None,
            "provenance": {"assurance": "legacy_unverified", "legacy_record": original},
        }

    def append(self, value: dict[str, Any]):
        event = validate_event(value)
        with self._locked():
            head, previous_hash, legacy_digest, legacy_count = self._head()
            integrity = self._verify_unlocked()
            if not integrity["ok"]:
                raise RuntimeError("refusing append to an invalid ledger: " + integrity["failure_category"])
            for existing_sequence, existing, existing_raw in self._scan():
                existing_public = self._public_entry(existing_sequence, existing, existing_raw)
                if existing_public.get("event_id") == event["event_id"]:
                    same = (digest(existing_public.get("payload")) == digest(event["payload"]) and
                            existing_public.get("event_type") == event["event_type"] and
                            existing_public.get("source") == event["source"] and
                            existing_public.get("timestamp") == event["timestamp"] and
                            existing_public.get("correlation_id") == event["correlation_id"])
                    if same:
                        return {"status": "duplicate", "entry": existing}
                    raise ValueError("event_id already exists with different content")
            seq = head + 1
            previous = previous_hash or (legacy_digest if head else None)
            record = {
                "schema_version": 1, "ledger_id": self.ledger_id, "sequence": seq,
                **event, "captured_at": now_utc(), "payload_sha256": digest(event["payload"]),
                "previous_entry_sha256": previous, "provenance": {},
            }
            record["entry_sha256"] = envelope_hash(record)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a+b") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                if size:
                    handle.seek(-1, os.SEEK_END)
                    if handle.read(1) != b"\n":
                        raise RuntimeError("cannot append: JSONL ledger has an unterminated final record")
                    handle.seek(0, os.SEEK_END)
                handle.write(canonical(record) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            return {"status": "stored", "entry": record}

    def pull(self, after_sequence=0, through_sequence=None, limit=100, **filters):
        with self._locked(shared=True):
            return self._pull_unlocked(after_sequence, through_sequence, limit, **filters)

    def _pull_unlocked(self, after_sequence=0, through_sequence=None, limit=100, **filters):
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        state = self._status_unlocked()
        head = state["head_sequence"]
        if after_sequence > head:
            raise ValueError("after_sequence exceeds head_sequence")
        upper = head if through_sequence is None else through_sequence
        if isinstance(upper, bool) or not isinstance(upper, int) or not 0 <= upper <= head or upper < after_sequence:
            raise ValueError("through_sequence must be between after_sequence and head_sequence")
        since, until = parse_time(filters.get("since")), parse_time(filters.get("until"))
        if since and until and since > until:
            raise ValueError("since must not be later than until")
        entries, scanned, scanned_count = [], after_sequence, 0
        for sequence, record, _ in self._scan():
            if sequence <= after_sequence:
                continue
            if sequence > upper:
                break
            scanned = sequence
            scanned_count += 1
            public = self._public_entry(sequence, record, _)
            matches = True
            if filters.get("event_id") and public.get("event_id") != filters["event_id"]: matches = False
            if filters.get("event_type") and public.get("event_type") != filters["event_type"]: matches = False
            if filters.get("source") and public.get("source") != filters["source"]: matches = False
            if filters.get("correlation_id") and public.get("correlation_id") != filters["correlation_id"]: matches = False
            stamp = public.get("timestamp", public.get("captured_at"))
            event_time = parse_time(stamp) if stamp else None
            if since and (event_time is None or event_time < since): matches = False
            if until and (event_time is None or event_time > until): matches = False
            if matches:
                entries.append(public)
            if scanned_count >= limit:
                break
        return {"entries": entries, "next_after_sequence": scanned, "head_sequence": head,
                "has_more": scanned < upper}

    def replay(self, after_sequence=0, through_sequence=None, limit=1000):
        return self.pull(after_sequence, through_sequence, limit)

    def export(self, after_sequence=0, through_sequence=None, limit=1000):
        return self.pull(after_sequence, through_sequence, limit)

    def get(self, event_id):
        with self._locked(shared=True):
            return self._get_unlocked(event_id)

    def _get_unlocked(self, event_id):
        for sequence, record, raw in self._scan():
            public = self._public_entry(sequence, record, raw)
            if public.get("event_id") == event_id:
                return public
        return None

    def verify(self, from_sequence=1, through_sequence=None, checkpoint=None):
        with self._locked(shared=True):
            return self._verify_unlocked(from_sequence, through_sequence, checkpoint)

    def _verify_unlocked(self, from_sequence=1, through_sequence=None, checkpoint=None):
        try:
            state = self._status_unlocked()
        except RuntimeError as exc:
            message = str(exc)
            try:
                failing_sequence = int(message.rsplit("line ", 1)[1].split()[0])
            except (IndexError, ValueError):
                failing_sequence = 1
            return {"ok": False, "first_failing_sequence": failing_sequence,
                    "failure_category": "malformed_record", "verified_count": 0,
                    "assurance": "legacy_unverified"}
        end = state["head_sequence"] if through_sequence is None else through_sequence
        if isinstance(from_sequence, bool) or not isinstance(from_sequence, int) or from_sequence < 1 or isinstance(end, bool) or not isinstance(end, int) or end > state["head_sequence"] or end < 0 or (end > 0 and end < from_sequence):
            raise ValueError("invalid verification range")
        if end == 0 and from_sequence == 1:
            return {"ok": True, "from_sequence": 1, "through_sequence": 0, "verified_count": 0, "assurance": "empty_ledger"}
        previous = None
        if from_sequence > 1:
            if not isinstance(checkpoint, dict) or checkpoint.get("ledger_id") != self.ledger_id or checkpoint.get("sequence") != from_sequence - 1:
                return {"ok": False, "first_failing_sequence": from_sequence,
                        "failure_category": "missing_checkpoint", "verified_count": 0,
                        "assurance": "unverified_partial_range"}
            previous = checkpoint.get("entry_sha256")
            if not isinstance(previous, str) or len(previous) != 64:
                previous = checkpoint.get("legacy_prefix_sha256")
                if (not isinstance(previous, str) or len(previous) != 64
                        or checkpoint.get("legacy_prefix_sequence_count") != from_sequence - 1):
                    return {"ok": False, "first_failing_sequence": from_sequence,
                            "failure_category": "invalid_checkpoint", "verified_count": 0,
                            "assurance": "unverified_partial_range"}
                legacy_checkpoint = True
            else:
                legacy_checkpoint = False
        else:
            legacy_checkpoint = False
        expected, verified, legacy_seen = from_sequence, 0, legacy_checkpoint
        legacy_digest = hashlib.sha256(b"").hexdigest()
        seen_event_ids = set()
        first_failure = None
        try:
            for sequence, record, raw in self._scan():
                if sequence > end:
                    break
                if sequence < from_sequence:
                    if from_sequence == 1:
                        if "entry_sha256" in record:
                            previous = record.get("entry_sha256")
                        else:
                            legacy_seen = True
                            legacy_digest = hashlib.sha256(bytes.fromhex(legacy_digest) + raw.rstrip(b"\r\n")).hexdigest()
                            previous = legacy_digest
                    continue
                if sequence != expected:
                    first_failure = (expected, "sequence_gap")
                    break
                public = self._public_entry(sequence, record, raw)
                if public["event_id"] in seen_event_ids:
                    first_failure = (sequence, "duplicate_event_id")
                    break
                seen_event_ids.add(public["event_id"])
                if "entry_sha256" not in record:
                    if "schema_version" in record or "sequence" in record:
                        first_failure = (sequence, "invalid_envelope")
                        break
                    legacy_seen = True
                    verified += 1
                    expected += 1
                    legacy_digest = hashlib.sha256(bytes.fromhex(legacy_digest) + raw.rstrip(b"\r\n")).hexdigest()
                    previous = legacy_digest
                    continue
                if record.get("previous_entry_sha256") != previous:
                    first_failure = (sequence, "broken_hash_chain")
                    break
                try:
                    validate_envelope(record, self.ledger_id)
                except ValueError as exc:
                    category = "payload_hash_mismatch" if "payload hash" in str(exc) else ("entry_hash_mismatch" if "entry hash" in str(exc) else "invalid_envelope")
                    first_failure = (sequence, category)
                    break
                previous = record["entry_sha256"]
                expected += 1
                verified += 1
        except RuntimeError as exc:
            message = str(exc)
            try:
                failing_sequence = int(message.rsplit("line ", 1)[1].split()[0])
            except (IndexError, ValueError):
                failing_sequence = expected
            first_failure = (failing_sequence, "malformed_record")
        if first_failure:
            return {"ok": False, "first_failing_sequence": first_failure[0], "failure_category": first_failure[1],
                    "verified_count": verified, "assurance": "legacy_prefix_unverified" if legacy_seen else "hash_chain"}
        if expected <= end:
            return {"ok": False, "first_failing_sequence": expected, "failure_category": "missing_sequence",
                    "verified_count": verified, "assurance": "legacy_prefix_unverified" if legacy_seen else "hash_chain"}
        return {"ok": True, "from_sequence": from_sequence, "through_sequence": end,
                "verified_count": verified,
                "assurance": "legacy_prefix_unverified_and_hash_chain" if legacy_seen else "hash_chain"}

    def import_batch(self, source_ledger_id, entries):
        if not isinstance(source_ledger_id, str) or not source_ledger_id or not isinstance(entries, list) or len(entries) > 1000:
            raise ValueError("source_ledger_id and up to 1000 entries are required")
        imported, duplicates, last_source = [], [], 0
        with self._locked():
            head, previous_hash, legacy_digest, legacy_count = self._head()
            integrity = self._verify_unlocked()
            if not integrity["ok"]:
                raise RuntimeError("refusing import into an invalid ledger: " + integrity["failure_category"])
            previous = previous_hash or (legacy_digest if head else None)
            prepared, seen = [], {}
            for incoming in entries:
                event = validate_event(incoming)
                source_sequence = incoming.get("sequence")
                if source_sequence is not None:
                    if isinstance(source_sequence, bool) or not isinstance(source_sequence, int) or source_sequence <= last_source:
                        raise ValueError("import source sequences must be positive and strictly ascending")
                    last_source = source_sequence
                if incoming.get("ledger_id") not in (None, source_ledger_id):
                    raise ValueError("import source ledger identity mismatch")
                if incoming.get("payload_sha256") is not None and incoming["payload_sha256"] != digest(event["payload"]):
                    raise ValueError("import payload hash mismatch")
                if incoming.get("entry_sha256") is not None:
                    validate_envelope(incoming, source_ledger_id)
                event_id = event["event_id"]
                prior = seen.get(event_id)
                if prior is not None:
                    if prior != event:
                        raise ValueError("import conflict: duplicate event_id has different content in batch")
                    duplicates.append(event_id)
                    continue
                seen[event_id] = event
                existing = self._get_unlocked(event_id)
                if existing:
                    same = existing.get("payload") == event["payload"] and all(
                        existing.get(k) == event[k] for k in ("event_type", "source", "timestamp", "correlation_id")
                    )
                    if same:
                        duplicates.append(event_id)
                        continue
                    raise ValueError("import conflict: event_id already exists with different content")
                prepared.append((incoming, event))
            # Validate the complete batch before the first durable write. JSONL
            # cannot provide a multi-record database transaction; each fsynced
            # line is a committed prefix and retries are idempotent.
            self.path.parent.mkdir(parents=True, exist_ok=True)
            committed_prefix = 0
            retry_after_source_sequence = None
            try:
                with self.path.open("a+b") as handle:
                    handle.seek(0, os.SEEK_END)
                    size = handle.tell()
                    if size:
                        handle.seek(-1, os.SEEK_END)
                        if handle.read(1) != b"\n":
                            raise RuntimeError("cannot import: JSONL ledger has an unterminated final record")
                        handle.seek(0, os.SEEK_END)
                    for incoming, event in prepared:
                        head += 1
                        provenance = {
                            "source_ledger_id": source_ledger_id,
                            "source_sequence": incoming.get("sequence"),
                            "source_entry_sha256": incoming.get("entry_sha256"),
                            "source_assurance": "entry_hash_verified" if incoming.get("entry_sha256") else "legacy_unverified",
                            "source_provenance": incoming.get("provenance", {}),
                        }
                        record = {
                            "schema_version": 1, "ledger_id": self.ledger_id, "sequence": head, **event,
                            "captured_at": incoming.get("captured_at") or now_utc(),
                            "payload_sha256": digest(event["payload"]), "previous_entry_sha256": previous,
                            "provenance": provenance,
                        }
                        record["entry_sha256"] = envelope_hash(record)
                        handle.write(canonical(record) + b"\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                        previous = record["entry_sha256"]
                        imported.append(event["event_id"])
                        committed_prefix += 1
                        retry_after_source_sequence = incoming.get("sequence", retry_after_source_sequence)
            except OSError as exc:
                raise LedgerImportError(
                    f"JSONL import stopped after committed prefix {committed_prefix}: {exc}",
                    committed_prefix, retry_after_source_sequence,
                ) from exc
        return {"imported": imported, "duplicates": duplicates, "head_sequence": head,
                "committed_prefix": len(imported),
                "retry_after_source_sequence": last_source if len(imported) == len(prepared) else (prepared[len(imported)][0].get("sequence") if len(imported) < len(prepared) else None)}
