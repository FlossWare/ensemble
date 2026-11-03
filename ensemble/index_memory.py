from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class InMemoryIndexStore:
    def __init__(self):
        self._sources: dict[str, dict[str, Any]] = {}
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
        return {"decision": decision, "source": dict(current)}

    async def record_tombstone(self, source_id: str, sequence: int) -> dict[str, Any]:
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
        return {"decision": decision, "source": dict(current)}

    async def get_source(self, source_id: str) -> dict[str, Any] | None:
        value = self._sources.get(source_id)
        return dict(value) if value else None

    async def list_sources(self, limit: int) -> list[dict[str, Any]]:
        return [dict(self._sources[key]) for key in sorted(self._sources)[:limit]]

    async def source_snapshot_upper_bound(self) -> str | None:
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
        return dict(value)

    async def record_generation_checkpoint(
        self, generation_id: str, capability: str,
        checkpoint: dict[str, Any], sealed: bool = False,
    ) -> dict[str, Any]:
        import json
        if capability not in {"index", "memory", "knowledge"}:
            raise ValueError("capability must be index, memory, or knowledge")
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
        if not all(target["checkpoints"].get(name, {}).get("sealed") is True for name in ("index", "memory", "knowledge")):
            raise RuntimeError("index, memory, and knowledge checkpoints must all be sealed")
        now = datetime.now(timezone.utc).isoformat()
        self._generations[self._active_generation_id]["state"] = "retired"
        self._generations[self._active_generation_id]["updated_at"] = now
        target.update(
            state="active", verification=dict(verification),
            activated_at=now, updated_at=now,
        )
        self._active_generation_id = generation_id
        self._generation_epoch += 1
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
        return dict(value)
