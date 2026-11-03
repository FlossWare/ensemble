from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class InMemoryIndexStore:
    def __init__(self):
        self._sources: dict[str, dict[str, Any]] = {}
        self._generation_sources: dict[str, dict[str, dict[str, Any]]] = {}
        self._generation_changes: dict[str, list[dict[str, Any]]] = {}
        self._change_sequence = 0
        self._write_fence_generation: str | None = None
        now = datetime.now(timezone.utc).isoformat()
        self._generations = {
            "generation-0001": {
                "generation_id": "generation-0001", "state": "active",
                "checkpoints": {}, "verification": {},
                "created_at": now, "updated_at": now, "activated_at": now,
            }
        }
        self._active_generation_id = "generation-0001"
        self._generation_epoch = 1

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def record_revision(self, source_id: str, sequence: int, revision_id: str, profiles: dict[str, Any]) -> dict[str, Any]:
        if self._write_fence_generation is not None:
            current = self._sources.get(source_id)
            return {"decision": "fenced", "source": dict(current) if current else None}
        current = self._sources.get(source_id)
        snapshot = dict(sorted(profiles.items()))
        if current is None:
            current = {
                "source_id": source_id, "high_sequence": sequence,
                "revision_id": revision_id, "profiles": snapshot,
                "tombstoned": False, "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._sources[source_id] = current
            decision = "accepted"
        elif current["tombstoned"]:
            decision = "tombstoned"
        elif sequence > current["high_sequence"]:
            current.update(
                high_sequence=sequence, revision_id=revision_id,
                profiles=snapshot, updated_at=datetime.now(timezone.utc).isoformat(),
            )
            decision = "accepted"
        elif sequence == current["high_sequence"]:
            decision = "duplicate" if current["revision_id"] == revision_id and current["profiles"] == snapshot else "conflict"
        else:
            decision = "superseded"
        if decision == "accepted":
            for generation in self._generations.values():
                if generation["state"] in {"active", "building"}:
                    self._change_sequence += 1
                    self._generation_changes.setdefault(generation["generation_id"], []).append({
                        "change_id": self._change_sequence, "generation_id": generation["generation_id"],
                        "source_id": source_id, "high_sequence": sequence, "revision_id": revision_id,
                        "profiles": snapshot, "tombstoned": False, "acknowledged": False,
                        "created_at": current["updated_at"],
                    })
        return {"decision": decision, "source": dict(current)}

    async def record_tombstone(self, source_id: str, sequence: int) -> dict[str, Any]:
        if self._write_fence_generation is not None:
            current = self._sources.get(source_id)
            return {"decision": "fenced", "source": dict(current) if current else None}
        current = self._sources.get(source_id)
        now = datetime.now(timezone.utc).isoformat()
        if current is None:
            current = {
                "source_id": source_id, "high_sequence": sequence,
                "revision_id": "tombstone", "profiles": {},
                "tombstoned": True, "updated_at": now,
            }
            self._sources[source_id] = current
            decision = "tombstoned"
        elif sequence >= current["high_sequence"]:
            current.update(high_sequence=max(sequence, current["high_sequence"]), tombstoned=True, updated_at=now)
            decision = "tombstoned"
        else:
            decision = "superseded"
        if decision == "tombstoned":
            for generation in self._generations.values():
                if generation["state"] in {"active", "building"}:
                    self._change_sequence += 1
                    self._generation_changes.setdefault(generation["generation_id"], []).append({
                        "change_id": self._change_sequence, "generation_id": generation["generation_id"],
                        "source_id": source_id, "high_sequence": current["high_sequence"],
                        "revision_id": current["revision_id"], "profiles": current["profiles"],
                        "tombstoned": True, "acknowledged": False, "created_at": current["updated_at"],
                    })
        return {"decision": decision, "source": dict(current)}

    async def get_source(self, source_id: str) -> dict[str, Any] | None:
        value = self._sources.get(source_id)
        return dict(value) if value else None

    async def list_sources(self, limit: int) -> list[dict[str, Any]]:
        return [dict(self._sources[key]) for key in sorted(self._sources)[:limit]]

    async def source_key_upper_bound(self) -> str | None:
        return max(self._sources) if self._sources else None

    async def list_sources_after(
        self, cursor: str | None, upper_bound: str | None, limit: int,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 10000:
            raise ValueError("limit must be between 1 and 10000")
        if upper_bound is None:
            return []
        keys = [
            key for key in sorted(self._sources)
            if key <= upper_bound and (cursor is None or key > cursor)
        ][:limit]
        return [dict(self._sources[key]) for key in keys]



    async def put_generation_source(self, generation_id: str, source: dict[str, Any]) -> dict[str, Any]:
        required = {"source_id", "high_sequence", "revision_id", "profiles", "tombstoned"}
        if not isinstance(source, dict) or not required.issubset(source):
            raise ValueError("source state is incomplete")
        if not isinstance(source["source_id"], str) or not source["source_id"] or len(source["source_id"]) > 512:
            raise ValueError("source_id must contain 1-512 characters")
        if not isinstance(source["profiles"], dict) or not isinstance(source["tombstoned"], bool):
            raise ValueError("profiles must be an object and tombstoned must be boolean")
        sequence = source["high_sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("high_sequence must be a positive integer")
        if not isinstance(source["revision_id"], str) or not source["revision_id"] or len(source["revision_id"]) > 512:
            raise ValueError("revision_id must contain 1-512 characters")
        import json
        profiles = dict(sorted(source["profiles"].items()))
        try:
            json.dumps(profiles, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("profiles must contain finite JSON values") from exc
        generation = self._generations.get(generation_id)
        if generation is None or generation["state"] != "building":
            raise RuntimeError("generation is not in building state")
        source_id = source["source_id"]
        records = self._generation_sources.setdefault(generation_id, {})
        current = records.get(source_id)
        now = datetime.now(timezone.utc).isoformat()
        tombstoned = bool(source["tombstoned"])
        if current is None:
            current = {
                "source_id": source_id, "high_sequence": sequence,
                "revision_id": source["revision_id"], "profiles": profiles,
                "tombstoned": tombstoned, "updated_at": now,
            }
            records[source_id] = current
            decision = "accepted"
        elif current["tombstoned"]:
            decision = "tombstoned"
        elif tombstoned:
            current.update(high_sequence=max(sequence, current["high_sequence"]), tombstoned=True, updated_at=now)
            decision = "tombstoned"
        elif sequence > current["high_sequence"]:
            current.update(
                high_sequence=sequence, revision_id=source["revision_id"],
                profiles=profiles, updated_at=now,
            )
            decision = "accepted"
        elif sequence == current["high_sequence"]:
            decision = "duplicate" if current["revision_id"] == source["revision_id"] and current["profiles"] == profiles else "conflict"
        else:
            decision = "superseded"
        return {"decision": decision, "source": dict(current)}

    async def get_generation_source(self, generation_id: str, source_id: str) -> dict[str, Any] | None:
        value = self._generation_sources.get(generation_id, {}).get(source_id)
        return dict(value) if value else None

    async def list_generation_sources_after(
        self, generation_id: str, cursor: str | None,
        upper_bound: str | None, limit: int,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 10000:
            raise ValueError("limit must be between 1 and 10000")
        if upper_bound is None:
            return []
        records = self._generation_sources.get(generation_id, {})
        keys = [
            key for key in sorted(records)
            if key <= upper_bound and (cursor is None or key > cursor)
        ][:limit]
        return [dict(records[key]) for key in keys]

    async def list_generation_changes_after(self, generation_id: str, after_change_id: int, limit: int) -> list[dict[str, Any]]:
        if isinstance(after_change_id, bool) or not isinstance(after_change_id, int) or after_change_id < 0:
            raise ValueError("after_change_id must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000")
        return [dict(item) for item in self._generation_changes.get(generation_id, [])
                if item["change_id"] > after_change_id][:limit]

    async def acknowledge_generation_change(self, source_id: str, sequence: int, tombstoned: bool) -> int:
        if not isinstance(source_id, str) or not source_id or len(source_id) > 512:
            raise ValueError("source_id must contain 1-512 characters")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("sequence must be a positive integer")
        if not isinstance(tombstoned, bool):
            raise ValueError("tombstoned must be a boolean")
        acknowledged = 0
        for generation in self._generations.values():
            if generation["state"] == "building":
                for change in self._generation_changes.get(generation["generation_id"], []):
                    if (change["source_id"] == source_id and change["high_sequence"] == sequence
                            and change["tombstoned"] is tombstoned and not change.get("acknowledged", False)):
                        change["acknowledged"] = True
                        acknowledged += 1
            elif generation["state"] == "active":
                self._generation_changes[generation["generation_id"]] = [
                    change for change in self._generation_changes.get(generation["generation_id"], [])
                    if not (change["source_id"] == source_id and change["high_sequence"] == sequence
                            and change["tombstoned"] is tombstoned)
                ]
        return acknowledged

    async def generation_change_high_watermark(self, generation_id: str) -> int:
        changes = self._generation_changes.get(generation_id, [])
        return changes[-1]["change_id"] if changes else 0

    async def acquire_generation_write_fence(self, generation_id: str, through_change_id: int) -> dict[str, Any]:
        if self._write_fence_generation not in (None, generation_id):
            raise RuntimeError("another generation owns the Index write fence")
        generation = self._generations.get(generation_id)
        if generation is None or generation["state"] != "building":
            raise RuntimeError("generation is not in building state")
        pending = [
            item for item in self._generation_changes.get(generation_id, [])
            if not item.get("acknowledged", False)
        ]
        if pending:
            raise RuntimeError("unacknowledged source mutations remain; do not fence generation writes")
        high = await self.generation_change_high_watermark(generation_id)
        if high != through_change_id:
            raise RuntimeError("generation change journal advanced; reconcile before acquiring the write fence")
        self._write_fence_generation = generation_id
        return {"generation_id": generation_id, "through_change_id": high,
                "acquired_at": datetime.now(timezone.utc).isoformat()}

    async def release_generation_write_fence(self, generation_id: str) -> bool:
        if self._write_fence_generation != generation_id:
            return False
        self._write_fence_generation = None
        return True

    async def generation_status(self) -> dict[str, Any]:
        return {
            "active_generation_id": self._active_generation_id,
            "epoch": self._generation_epoch,
            "generations": [
                dict(value) for value in sorted(
                    self._generations.values(),
                    key=lambda item: item["created_at"], reverse=True,
                )
            ],
        }

    async def create_generation(self, generation_id: str) -> dict[str, Any]:
        if not isinstance(generation_id, str) or not generation_id or len(generation_id) > 64:
            raise ValueError("generation_id must contain 1-64 characters")
        if any(item["state"] == "building" for item in self._generations.values()):
            raise RuntimeError("an index generation rebuild is already in progress")
        if generation_id in self._generations:
            raise ValueError("generation_id already exists")
        now = datetime.now(timezone.utc).isoformat()
        value = {
            "generation_id": generation_id, "state": "building",
            "checkpoints": {}, "verification": {},
            "created_at": now, "updated_at": now, "activated_at": None,
        }
        self._generations[generation_id] = value
        pending = [
            dict(item) for item in self._generation_changes.get(self._active_generation_id, [])
            if not item.get("acknowledged", False)
        ]
        self._generation_changes[generation_id] = []
        for item in pending:
            self._change_sequence += 1
            copied = dict(item)
            copied.update(change_id=self._change_sequence, generation_id=generation_id, acknowledged=False)
            self._generation_changes[generation_id].append(copied)
        return dict(value)

    async def record_generation_checkpoint(
        self, generation_id: str, capability: str,
        checkpoint: dict[str, Any], sealed: bool = False,
    ) -> dict[str, Any]:
        import json
        if capability not in {"index", "documents", "vectors", "memory", "knowledge", "index_reconciliation"}:
            raise ValueError("capability must be index, documents, vectors, memory, knowledge, or index_reconciliation")
        if not isinstance(checkpoint, dict):
            raise ValueError("checkpoint must be an object")
        try:
            json.dumps(checkpoint, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("checkpoint must contain finite JSON values") from exc
        value = self._generations.get(generation_id)
        if value is None or value["state"] != "building":
            raise RuntimeError("generation is not in building state")
        previous = value["checkpoints"].get(capability)
        if previous and previous.get("sealed"):
            raise RuntimeError(f"{capability} checkpoint is already sealed")
        value["checkpoints"][capability] = {
            "value": dict(checkpoint), "sealed": bool(sealed),
        }
        value["updated_at"] = datetime.now(timezone.utc).isoformat()
        return dict(value)

    async def activate_generation(
        self, generation_id: str, expected_active_generation_id: str,
        expected_epoch: int, verification: dict[str, Any],
    ) -> dict[str, Any]:
        required = {"acknowledged_writes_verified", "tombstones_verified"}
        if not isinstance(verification, dict) or not all(verification.get(key) is True for key in required):
            raise ValueError("activation requires acknowledged-write and tombstone verification")
        if self._active_generation_id != expected_active_generation_id or self._generation_epoch != expected_epoch:
            raise RuntimeError("active generation changed; refresh status before cutover")
        target = self._generations.get(generation_id)
        if target is None or target["state"] != "building":
            raise RuntimeError("target generation is not building")
        if not all(target["checkpoints"].get(name, {}).get("sealed") is True for name in ("index", "documents", "vectors", "memory", "knowledge", "index_reconciliation")):
            raise RuntimeError("index, documents, vectors, memory, knowledge, and index reconciliation checkpoints must all be sealed")
        if self._write_fence_generation != generation_id:
            raise RuntimeError("Index write fence must be held during generation activation")
        high = await self.generation_change_high_watermark(generation_id)
        if verification.get("reconciled_through_change_id") != high:
            raise RuntimeError("generation change journal has not been fully reconciled")
        now = datetime.now(timezone.utc).isoformat()
        previous_active_generation_id = self._active_generation_id
        self._generations[previous_active_generation_id]["state"] = "retired"
        self._generations[previous_active_generation_id]["updated_at"] = now
        target.update(
            state="active", verification=dict(verification),
            activated_at=now, updated_at=now,
        )
        self._active_generation_id = generation_id
        self._generation_epoch += 1
        self._write_fence_generation = None
        self._generation_changes[previous_active_generation_id] = []
        self._generation_changes[generation_id] = []
        return {
            "active_generation_id": self._active_generation_id,
            "epoch": self._generation_epoch,
        }

    async def abort_generation(self, generation_id: str, reason: str) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2048:
            raise ValueError("reason must contain 1-2048 characters")
        value = self._generations.get(generation_id)
        if value is None or value["state"] != "building":
            raise RuntimeError("only a building generation can be aborted")
        value.update(
            state="failed", verification={"failure": reason},
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        if self._write_fence_generation == generation_id:
            self._write_fence_generation = None
        return dict(value)
