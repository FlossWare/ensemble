from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class InMemoryIndexStore:
    def __init__(self):
        self._sources: dict[str, dict[str, Any]] = {}

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
