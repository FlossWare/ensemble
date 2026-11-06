from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import Column, DateTime, Integer, JSON, MetaData, String, Table, and_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.engine import make_url

from .partitioning import PartitionedAsyncEngine, current_request_scope


collaboration_metadata = MetaData()
collaboration_records = Table(
    "ensemble_collaboration_records",
    collaboration_metadata,
    Column("record_id", String(64), primary_key=True),
    Column("record_type", String(32), nullable=False),
    Column("project_id", String(128), nullable=True),
    Column("actor_id", String(256), nullable=True),
    Column("status", String(32), nullable=True),
    Column("identity_key", String(512), nullable=True, unique=True),
    Column("payload", JSON, nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


class CollaborationStore(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def create(self, record_type: str, payload: dict[str, Any], *, record_id: str | None = None, project_id: str | None = None, actor_id: str | None = None, status: str | None = None, identity_key: str | None = None) -> dict[str, Any]: ...
    async def get(self, record_type: str, record_id: str) -> dict[str, Any] | None: ...
    async def list(self, record_type: str | None = None, *, project_id: str | None = None, actor_id: str | None = None, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]: ...
    async def update(self, record_type: str, record_id: str, *, expected_version: int, payload: dict[str, Any], status: str | None = None) -> dict[str, Any] | None: ...


class SqliteCollaborationStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.getenv(
            "ENSEMBLE_DATABASE_URL", "sqlite+aiosqlite:///./.ensemble/ensemble.db"
        )
        if make_url(self.database_url).get_backend_name() != "sqlite":
            raise ValueError("Ensemble collaboration persistence supports SQLite only")
        self.engine: AsyncEngine = PartitionedAsyncEngine(self.database_url, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            names = await connection.run_sync(
                lambda conn: __import__("sqlalchemy").inspect(conn).get_table_names()
            )
        if "ensemble_collaboration_records" not in names:
            raise RuntimeError("collaboration schema is not initialized; run 'alembic upgrade head'")

    async def close(self) -> None:
        await self.engine.dispose()

    @staticmethod
    def _record(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["id"] = result.pop("record_id")
        result["type"] = result.pop("record_type")
        return result

    async def create(self, record_type: str, payload: dict[str, Any], *, record_id: str | None = None, project_id: str | None = None, actor_id: str | None = None, status: str | None = None, identity_key: str | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        values = {
            "record_id": record_id or str(uuid4()), "record_type": record_type,
            "project_id": project_id, "actor_id": actor_id, "status": status,
            "identity_key": identity_key, "payload": payload, "version": 1,
            "created_at": now, "updated_at": now,
        }
        try:
            async with self.engine.begin() as connection:
                await connection.execute(collaboration_records.insert().values(**values))
                row = (await connection.execute(
                    select(collaboration_records).where(
                        collaboration_records.c.record_id == values["record_id"]
                    )
                )).mappings().one()
        except IntegrityError as exc:
            raise ValueError("record or identity already exists") from exc
        return self._record(row)

    async def get(self, record_type: str, record_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(
                select(collaboration_records).where(
                    collaboration_records.c.record_type == record_type,
                    collaboration_records.c.record_id == record_id,
                )
            )).mappings().first()
        return self._record(row) if row else None

    async def list(self, record_type: str | None = None, *, project_id: str | None = None, actor_id: str | None = None, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        filters = []
        if record_type is not None:
            filters.append(collaboration_records.c.record_type == record_type)
        if project_id is not None:
            filters.append(collaboration_records.c.project_id == project_id)
        if actor_id is not None:
            filters.append(collaboration_records.c.actor_id == actor_id)
        if status is not None:
            filters.append(collaboration_records.c.status == status)
        statement = select(collaboration_records)
        if filters:
            statement = statement.where(and_(*filters))
        statement = statement.order_by(
            collaboration_records.c.created_at.desc(),
            collaboration_records.c.record_id.desc(),
        ).limit(limit)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [self._record(row) for row in rows]

    async def update(self, record_type: str, record_id: str, *, expected_version: int, payload: dict[str, Any], status: str | None = None) -> dict[str, Any] | None:
        values: dict[str, Any] = {
            "payload": payload, "updated_at": datetime.now(timezone.utc),
            "version": collaboration_records.c.version + 1,
        }
        if status is not None:
            values["status"] = status
        async with self.engine.begin() as connection:
            result = await connection.execute(
                update(collaboration_records).where(
                    collaboration_records.c.record_type == record_type,
                    collaboration_records.c.record_id == record_id,
                    collaboration_records.c.version == expected_version,
                ).values(**values)
            )
            if result.rowcount != 1:
                return None
            row = (await connection.execute(
                select(collaboration_records).where(
                    collaboration_records.c.record_type == record_type,
                    collaboration_records.c.record_id == record_id,
                )
            )).mappings().one()
        return self._record(row)


class InMemoryCollaborationStore:
    def __init__(self):
        self._records: dict[str, dict[str, Any]] = {}
        self._identity: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create(self, record_type: str, payload: dict[str, Any], *, record_id: str | None = None, project_id: str | None = None, actor_id: str | None = None, status: str | None = None, identity_key: str | None = None) -> dict[str, Any]:
        async with self._lock:
            key = record_id or str(uuid4())
            if key in self._records or (identity_key and identity_key in self._identity):
                raise ValueError("record or identity already exists")
            now = datetime.now(timezone.utc)
            record = {
                "id": key, "type": record_type, "project_id": project_id,
                "actor_id": actor_id, "status": status, "identity_key": identity_key,
                "payload": dict(payload), "version": 1,
                "created_at": now, "updated_at": now,
            }
            self._records[key] = record
            if identity_key:
                self._identity[identity_key] = key
            return dict(record)

    async def get(self, record_type: str, record_id: str) -> dict[str, Any] | None:
        async with self._lock:
            record = self._records.get(record_id)
            return dict(record) if record and record["type"] == record_type else None

    async def list(self, record_type: str | None = None, *, project_id: str | None = None, actor_id: str | None = None, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        async with self._lock:
            rows = [
                dict(record) for record in self._records.values()
                if (record_type is None or record["type"] == record_type)
                and (project_id is None or record["project_id"] == project_id)
                and (actor_id is None or record["actor_id"] == actor_id)
                and (status is None or record["status"] == status)
            ]
        return sorted(rows, key=lambda item: (item["created_at"], item["id"]), reverse=True)[:limit]

    async def update(self, record_type: str, record_id: str, *, expected_version: int, payload: dict[str, Any], status: str | None = None) -> dict[str, Any] | None:
        async with self._lock:
            record = self._records.get(record_id)
            if not record or record["type"] != record_type or record["version"] != expected_version:
                return None
            record["payload"] = dict(payload)
            record["version"] += 1
            record["updated_at"] = datetime.now(timezone.utc)
            if status is not None:
                record["status"] = status
            return dict(record)


class SynthesisStrategy(Protocol):
    def generate(self, contributions: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


class ContributionSummarySynthesisStrategy:
    """Deterministic default; callers may inject a reviewed synthesis implementation."""

    def generate(self, contributions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{
            "text": item["payload"]["summary"],
            "contribution_ids": [item["id"]],
            "source_refs": [item["payload"]["source_ref"]] if item["payload"].get("source_ref") else [],
            "actor_id": item["actor_id"],
        } for item in reversed(contributions)]


def create_collaboration_router(store: CollaborationStore, synthesis_strategy: SynthesisStrategy | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/collaboration", tags=["collaboration"])
    active_synthesis_strategy = synthesis_strategy or ContributionSummarySynthesisStrategy()
    roles = {"read", "contribute", "review", "approve", "owner"}

    def required_string(payload: dict[str, Any], name: str, maximum: int, *, optional: bool = False) -> str | None:
        value = payload.get(name)
        if optional and value is None:
            return None
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise HTTPException(status_code=400, detail=f"{name} must be a non-empty string of at most {maximum} characters")
        return value.strip()

    def string_list(payload: dict[str, Any], name: str, maximum_items: int = 100) -> list[str]:
        value = payload.get(name, [])
        if not isinstance(value, list) or len(value) > maximum_items or any(
            not isinstance(item, str) or not item.strip() or len(item) > 2048 for item in value
        ):
            raise HTTPException(status_code=400, detail=f"{name} must be an array of at most {maximum_items} non-empty strings")
        return [item.strip() for item in value]

    def actor_id(payload: dict[str, Any], request: Request) -> str:
        actor = required_string(payload, "actor_id", 256)
        scope = current_request_scope()
        if scope.authorization and actor != scope.principal_id:
            raise HTTPException(status_code=403, detail="actor_id must match the authenticated principal")
        return actor

    def validated_viewer_id(value: str, request: Request) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise HTTPException(status_code=400, detail="viewer_id must be a non-empty participant identifier")
        scope = current_request_scope()
        if scope.authorization and value != scope.principal_id:
            raise HTTPException(status_code=403, detail="viewer_id must match the authenticated principal")
        return value

    async def participant(participant_id: str) -> dict[str, Any]:
        result = await store.get("participant", participant_id)
        if result is None:
            raise HTTPException(status_code=404, detail="unknown participant")
        return result

    async def project(project_id: str) -> dict[str, Any]:
        result = await store.get("project", project_id)
        if result is None:
            raise HTTPException(status_code=404, detail="unknown project")
        return result

    async def membership(project_id: str, participant_id: str) -> dict[str, Any] | None:
        key = f"{project_id}:{participant_id}"
        rows = await store.list("membership", project_id=project_id, limit=500)
        return next((item for item in rows if item.get("identity_key") == key), None)

    async def require_role(project_id: str, participant_id: str, allowed: set[str]) -> dict[str, Any]:
        await project(project_id)
        member = await membership(project_id, participant_id)
        role = member["payload"]["role"] if member else None
        if role not in allowed and role != "owner":
            raise HTTPException(status_code=403, detail="participant lacks the required project role")
        return member

    async def event(handoff: dict[str, Any], kind: str, actor: str, details: dict[str, Any] | None = None) -> None:
        await store.create(
            "handoff_event", {"handoff_id": handoff["id"], "event": kind, "details": details or {}},
            project_id=handoff["project_id"], actor_id=actor, status=kind,
        )

    @router.post("/participants")
    async def create_participant(payload: dict[str, Any]):
        display_name = required_string(payload, "display_name", 256)
        participant_type = required_string(payload, "participant_type", 16)
        if participant_type not in {"human", "agent"}:
            raise HTTPException(status_code=400, detail="participant_type must be human or agent")
        provider = required_string(payload, "provider", 128, optional=True)
        model = required_string(payload, "model", 256, optional=True)
        participant_id = required_string(payload, "participant_id", 256, optional=True) or str(uuid4())
        try:
            record = await store.create("participant", {
                "display_name": display_name, "participant_type": participant_type,
                "provider": provider, "model": model,
            }, record_id=participant_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "participant": record}

    @router.get("/participants/{participant_id}")
    async def get_participant(participant_id: str):
        return {"ok": True, "participant": await participant(participant_id)}

    @router.post("/projects")
    async def create_project(payload: dict[str, Any], request: Request):
        name = required_string(payload, "name", 256)
        owner_id = required_string(payload, "owner_id", 256)
        scope = current_request_scope()
        if scope.authorization and owner_id != scope.principal_id:
            raise HTTPException(status_code=403, detail="owner_id must match the authenticated principal")
        await participant(owner_id)
        description = required_string(payload, "description", 4000, optional=True)
        human_approval_required = payload.get("human_approval_required", True)
        if not isinstance(human_approval_required, bool):
            raise HTTPException(status_code=400, detail="human_approval_required must be a boolean")
        record = await store.create("project", {
            "name": name, "description": description,
            "owner_id": owner_id, "human_approval_required": human_approval_required,
        })
        await store.create(
            "membership", {"participant_id": owner_id, "role": "owner"},
            project_id=record["id"], actor_id=owner_id,
            identity_key=f"{record['id']}:{owner_id}",
        )
        return {"ok": True, "project": record}

    @router.get("/projects/{project_id}")
    async def get_project(project_id: str, viewer_id: str, request: Request):
        viewer = viewer_id_value = viewer_id
        viewer = viewer_id(viewer_id_value, request)
        await require_role(project_id, viewer, roles)
        return {"ok": True, "project": await project(project_id)}

    @router.post("/projects/{project_id}/members")
    async def add_member(project_id: str, payload: dict[str, Any], request: Request):
        actor = actor_id(payload, request)
        await require_role(project_id, actor, {"owner", "approve"})
        member_id = required_string(payload, "participant_id", 256)
        await participant(member_id)
        role = required_string(payload, "role", 16)
        if role not in roles:
            raise HTTPException(status_code=400, detail="role must be read, contribute, review, approve, or owner")
        try:
            member = await store.create(
                "membership", {"participant_id": member_id, "role": role},
                project_id=project_id, actor_id=member_id,
                identity_key=f"{project_id}:{member_id}",
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="participant is already a project member") from exc
        return {"ok": True, "membership": member}

    @router.get("/projects/{project_id}/members")
    async def list_members(project_id: str, viewer_id: str, request: Request, limit: int = 100):
        await require_role(project_id, validated_viewer_id(viewer_id, request), roles)
        return {"ok": True, "members": await store.list("membership", project_id=project_id, limit=limit)}

    @router.post("/projects/{project_id}/contributions")
    async def create_contribution(project_id: str, payload: dict[str, Any], request: Request):
        actor = actor_id(payload, request)
        await require_role(project_id, actor, {"contribute", "review"})
        kind = required_string(payload, "kind", 64)
        summary = required_string(payload, "summary", 4000)
        work_item_id = required_string(payload, "work_item_id", 256, optional=True)
        source_ref = required_string(payload, "source_ref", 2048, optional=True)
        review_status = payload.get("review_status", "unreviewed")
        if review_status not in {"unreviewed", "accepted", "rejected"}:
            raise HTTPException(status_code=400, detail="review_status must be unreviewed, accepted, or rejected")
        record = await store.create(
            "contribution", {
                "work_item_id": work_item_id, "kind": kind, "summary": summary,
                "source_ref": source_ref, "review_status": review_status,
            }, project_id=project_id, actor_id=actor, status=review_status,
        )
        return {"ok": True, "contribution": record}

    @router.get("/projects/{project_id}/contributions")
    async def list_contributions(project_id: str, viewer_id: str, request: Request, actor_id: str | None = None, status: str | None = None, limit: int = 100):
        await require_role(project_id, viewer_id(viewer_id, request), roles)
        return {"ok": True, "contributions": await store.list(
            "contribution", project_id=project_id, actor_id=actor_id, status=status, limit=limit,
        )}

    @router.post("/projects/{project_id}/handoffs")
    async def create_handoff(project_id: str, payload: dict[str, Any], request: Request):
        sender = actor_id(payload, request)
        await require_role(project_id, sender, {"contribute", "review"})
        recipient = required_string(payload, "recipient_id", 256, optional=True)
        if recipient:
            await participant(recipient)
            await require_role(project_id, recipient, {"read", "contribute", "review", "approve"})
        objective = required_string(payload, "objective", 4000)
        completed_work = required_string(payload, "completed_work", 8000, optional=True)
        current_state = required_string(payload, "current_state", 8000)
        work_item_id = required_string(payload, "work_item_id", 256, optional=True)
        blockers = string_list(payload, "blockers")
        next_actions = string_list(payload, "next_actions")
        references = string_list(payload, "references")
        record = await store.create(
            "handoff", {
                "work_item_id": work_item_id, "sender_id": sender,
                "recipient_id": recipient, "objective": objective,
                "completed_work": completed_work, "current_state": current_state,
                "blockers": blockers, "next_actions": next_actions,
                "references": references, "supersedes_id": payload.get("supersedes_id"),
            }, project_id=project_id, actor_id=sender, status="open",
        )
        await event(record, "created", sender)
        return {"ok": True, "handoff": record}

    @router.get("/projects/{project_id}/handoffs")
    async def list_handoffs(project_id: str, viewer_id: str, request: Request, status: str | None = None, limit: int = 100):
        await require_role(project_id, viewer_id(viewer_id, request), roles)
        return {"ok": True, "handoffs": await store.list("handoff", project_id=project_id, status=status, limit=limit)}

    @router.get("/handoffs/{handoff_id}")
    async def get_handoff(handoff_id: str, viewer_id: str, request: Request):
        handoff = await store.get("handoff", handoff_id)
        if handoff is None:
            raise HTTPException(status_code=404, detail="unknown handoff")
        await require_role(handoff["project_id"], viewer_id(viewer_id, request), roles)
        history = await store.list("handoff_event", project_id=handoff["project_id"], limit=500)
        return {"ok": True, "handoff": handoff, "history": [
            item for item in history if item["payload"].get("handoff_id") == handoff_id
        ]}

    async def transition_handoff(handoff_id: str, payload: dict[str, Any], request: Request, expected: str, target: str):
        actor = actor_id(payload, request)
        handoff = await store.get("handoff", handoff_id)
        if handoff is None:
            raise HTTPException(status_code=404, detail="unknown handoff")
        await require_role(handoff["project_id"], actor, {"contribute", "review"})
        recipient = handoff["payload"].get("recipient_id")
        if recipient and target in {"acknowledged", "completed"} and recipient != actor:
            raise HTTPException(status_code=403, detail="only the intended recipient may acknowledge or complete this handoff")
        if handoff["status"] != expected:
            raise HTTPException(status_code=409, detail=f"handoff transition requires status {expected}")
        payload_state = dict(handoff["payload"])
        payload_state[target + "_by"] = actor
        payload_state[target + "_at"] = datetime.now(timezone.utc).isoformat()
        updated = await store.update(
            "handoff", handoff_id, expected_version=handoff["version"],
            payload=payload_state, status=target,
        )
        if updated is None:
            raise HTTPException(status_code=409, detail="handoff changed concurrently; reload and retry")
        await event(updated, target, actor)
        return {"ok": True, "handoff": updated}

    @router.post("/handoffs/{handoff_id}/checkpoints")
    async def checkpoint_handoff(handoff_id: str, payload: dict[str, Any], request: Request):
        actor = actor_id(payload, request)
        handoff = await store.get("handoff", handoff_id)
        if handoff is None:
            raise HTTPException(status_code=404, detail="unknown handoff")
        await require_role(handoff["project_id"], actor, {"contribute", "review"})
        if handoff["status"] not in {"open", "acknowledged"}:
            raise HTTPException(status_code=409, detail="checkpoints require an open or acknowledged handoff")
        checkpoint = {
            "current_state": required_string(payload, "current_state", 8000),
            "completed_work": required_string(payload, "completed_work", 8000, optional=True),
            "blockers": string_list(payload, "blockers"),
            "next_actions": string_list(payload, "next_actions"),
            "references": string_list(payload, "references"),
        }
        updated_payload = dict(handoff["payload"])
        updated_payload.update(checkpoint)
        updated_payload["last_checkpoint_by"] = actor
        updated_payload["last_checkpoint_at"] = datetime.now(timezone.utc).isoformat()
        updated = await store.update(
            "handoff", handoff_id, expected_version=handoff["version"],
            payload=updated_payload,
        )
        if updated is None:
            raise HTTPException(status_code=409, detail="handoff changed concurrently; reload and retry")
        checkpoint_record = await store.create(
            "handoff_checkpoint", {"handoff_id": handoff_id, **checkpoint},
            project_id=handoff["project_id"], actor_id=actor,
        )
        await event(updated, "checkpoint", actor, {"checkpoint_id": checkpoint_record["id"]})
        return {"ok": True, "handoff": updated, "checkpoint": checkpoint_record}

    @router.post("/handoffs/{handoff_id}/acknowledge")
    async def acknowledge_handoff(handoff_id: str, payload: dict[str, Any], request: Request):
        return await transition_handoff(handoff_id, payload, request, "open", "acknowledged")

    @router.post("/handoffs/{handoff_id}/complete")
    async def complete_handoff(handoff_id: str, payload: dict[str, Any], request: Request):
        return await transition_handoff(handoff_id, payload, request, "acknowledged", "completed")

    @router.post("/handoffs/{handoff_id}/supersede")
    async def supersede_handoff(handoff_id: str, payload: dict[str, Any], request: Request):
        actor = actor_id(payload, request)
        previous = await store.get("handoff", handoff_id)
        if previous is None:
            raise HTTPException(status_code=404, detail="unknown handoff")
        await require_role(previous["project_id"], actor, {"contribute", "review"})
        if previous["status"] not in {"open", "acknowledged"}:
            raise HTTPException(status_code=409, detail="only open or acknowledged handoffs can be superseded")
        replacement_payload = dict(payload)
        replacement_payload["actor_id"] = actor
        replacement_payload["supersedes_id"] = handoff_id
        replacement_payload.pop("recipient_id", None)
        replacement = await create_handoff(previous["project_id"], replacement_payload, request)
        old_payload = dict(previous["payload"])
        old_payload["superseded_by"] = replacement["handoff"]["id"]
        updated = await store.update(
            "handoff", handoff_id, expected_version=previous["version"],
            payload=old_payload, status="superseded",
        )
        if updated is None:
            raise HTTPException(status_code=409, detail="handoff changed concurrently; replacement remains recorded")
        await event(updated, "superseded", actor, {"replacement_id": replacement["handoff"]["id"]})
        return {"ok": True, "handoff": updated, "replacement": replacement["handoff"]}

    async def build_synthesis(project_id: str, actor: str, contribution_ids: list[str] | None):
        contributions = await store.list("contribution", project_id=project_id, limit=500)
        if contribution_ids is not None:
            wanted = set(contribution_ids)
            contributions = [item for item in contributions if item["id"] in wanted]
            if wanted != {item["id"] for item in contributions}:
                raise HTTPException(status_code=400, detail="all contribution_ids must belong to this project")
        statements = active_synthesis_strategy.generate(contributions)
        return await store.create(
            "synthesis", {
                "statements": statements,
                "contribution_ids": [item["id"] for item in reversed(contributions)],
                "supersedes_id": None,
            }, project_id=project_id, actor_id=actor,
        )

    @router.post("/projects/{project_id}/syntheses")
    async def create_synthesis(project_id: str, payload: dict[str, Any], request: Request):
        actor = actor_id(payload, request)
        await require_role(project_id, actor, {"contribute", "review"})
        ids = payload.get("contribution_ids")
        if ids is not None and (not isinstance(ids, list) or any(not isinstance(item, str) for item in ids)):
            raise HTTPException(status_code=400, detail="contribution_ids must be an array of identifiers")
        synthesis = await build_synthesis(project_id, actor, ids)
        return {"ok": True, "synthesis": synthesis}

    @router.post("/syntheses/{synthesis_id}/refresh")
    async def refresh_synthesis(synthesis_id: str, payload: dict[str, Any], request: Request):
        actor = actor_id(payload, request)
        previous = await store.get("synthesis", synthesis_id)
        if previous is None:
            raise HTTPException(status_code=404, detail="unknown synthesis")
        await require_role(previous["project_id"], actor, {"contribute", "review"})
        ids = payload.get("contribution_ids")
        if ids is not None and (not isinstance(ids, list) or any(not isinstance(item, str) for item in ids)):
            raise HTTPException(status_code=400, detail="contribution_ids must be an array of identifiers")
        refreshed = await build_synthesis(previous["project_id"], actor, ids)
        refreshed_payload = dict(refreshed["payload"])
        refreshed_payload["supersedes_id"] = synthesis_id
        updated = await store.update(
            "synthesis", refreshed["id"], expected_version=refreshed["version"],
            payload=refreshed_payload,
        )
        return {"ok": True, "synthesis": updated or refreshed}

    @router.get("/projects/{project_id}/syntheses")
    async def list_syntheses(project_id: str, viewer_id: str, request: Request, limit: int = 100):
        await require_role(project_id, viewer_id(viewer_id, request), roles)
        return {"ok": True, "syntheses": await store.list("synthesis", project_id=project_id, limit=limit)}

    @router.post("/projects/{project_id}/proposals")
    async def create_proposal(project_id: str, payload: dict[str, Any], request: Request):
        actor = actor_id(payload, request)
        await require_role(project_id, actor, {"contribute", "review"})
        title = required_string(payload, "title", 256)
        summary = required_string(payload, "summary", 4000)
        rationale = required_string(payload, "rationale", 8000)
        objections = string_list(payload, "objections")
        contribution_ids = string_list(payload, "contribution_ids")
        for contribution_id in contribution_ids:
            contribution = await store.get("contribution", contribution_id)
            if contribution is None or contribution["project_id"] != project_id:
                raise HTTPException(status_code=400, detail="proposal contribution_ids must belong to this project")
        gated = payload.get("human_approval_required")
        if gated is None:
            gated = (await project(project_id))["payload"].get("human_approval_required", True)
        if not isinstance(gated, bool):
            raise HTTPException(status_code=400, detail="human_approval_required must be a boolean")
        proposal = await store.create(
            "proposal", {
                "title": title, "summary": summary, "rationale": rationale,
                "objections": objections, "contribution_ids": contribution_ids, "human_approval_required": gated,
            }, project_id=project_id, actor_id=actor, status="proposed",
        )
        return {"ok": True, "proposal": proposal}

    @router.get("/projects/{project_id}/proposals")
    async def list_proposals(project_id: str, viewer_id: str, request: Request, status: str | None = None, limit: int = 100):
        await require_role(project_id, viewer_id(viewer_id, request), roles)
        return {"ok": True, "proposals": await store.list("proposal", project_id=project_id, status=status, limit=limit)}

    @router.get("/proposals/{proposal_id}")
    async def get_proposal(proposal_id: str, viewer_id: str, request: Request):
        proposal = await store.get("proposal", proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="unknown proposal")
        await require_role(proposal["project_id"], viewer_id(viewer_id, request), roles)
        objections = await store.list("proposal_objection", project_id=proposal["project_id"], limit=500)
        decisions = await store.list("decision", project_id=proposal["project_id"], limit=500)
        return {"ok": True, "proposal": proposal, "objections": [
            item for item in objections if item["payload"].get("proposal_id") == proposal_id
        ], "decisions": [
            item for item in decisions if item["payload"].get("proposal_id") == proposal_id
        ]}

    @router.post("/proposals/{proposal_id}/objections")
    async def add_objection(proposal_id: str, payload: dict[str, Any], request: Request):
        actor = actor_id(payload, request)
        proposal = await store.get("proposal", proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="unknown proposal")
        await require_role(proposal["project_id"], actor, {"contribute", "review"})
        text = required_string(payload, "text", 4000)
        refs = string_list(payload, "contribution_ids")
        for contribution_id in refs:
            contribution = await store.get("contribution", contribution_id)
            if contribution is None or contribution["project_id"] != proposal["project_id"]:
                raise HTTPException(status_code=400, detail="objection references must identify project contributions")
        objection = await store.create(
            "proposal_objection", {"proposal_id": proposal_id, "text": text, "contribution_ids": refs},
            project_id=proposal["project_id"], actor_id=actor,
        )
        return {"ok": True, "objection": objection}

    @router.post("/proposals/{proposal_id}/decisions")
    async def decide_proposal(proposal_id: str, payload: dict[str, Any], request: Request):
        actor = actor_id(payload, request)
        proposal = await store.get("proposal", proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="unknown proposal")
        await require_role(proposal["project_id"], actor, {"review", "approve"})
        decision_status = required_string(payload, "status", 16)
        if decision_status not in {"accepted", "rejected", "under_review", "superseded"}:
            raise HTTPException(status_code=400, detail="status must be accepted, rejected, under_review, or superseded")
        rationale = required_string(payload, "rationale", 8000)
        decision_contribution_ids = payload.get("contribution_ids", proposal["payload"].get("contribution_ids", []))
        if not isinstance(decision_contribution_ids, list) or any(not isinstance(item, str) for item in decision_contribution_ids):
            raise HTTPException(status_code=400, detail="contribution_ids must be an array of identifiers")
        for contribution_id in decision_contribution_ids:
            contribution = await store.get("contribution", contribution_id)
            if contribution is None or contribution["project_id"] != proposal["project_id"]:
                raise HTTPException(status_code=400, detail="decision contribution_ids must belong to this project")
        decision_maker = await participant(actor)
        gated = proposal["payload"].get("human_approval_required", True)
        human_approved = decision_maker["payload"]["participant_type"] == "human" and (
            (await membership(proposal["project_id"], actor)) or {}
        ).get("payload", {}).get("role") in {"approve", "owner"}
        if decision_status in {"accepted", "rejected"} and gated and not human_approved:
            raise HTTPException(status_code=403, detail="this proposal requires an authorized human decision")
        if decision_status in {"accepted", "rejected"} and not gated:
            role = ((await membership(proposal["project_id"], actor)) or {}).get("payload", {}).get("role")
            if role not in {"approve", "owner"}:
                raise HTTPException(status_code=403, detail="participant lacks decision authority")
        decision = await store.create(
            "decision", {
                "proposal_id": proposal_id, "status": decision_status,
                "rationale": rationale, "decision_maker_id": actor,
                "contribution_ids": decision_contribution_ids,
                "human_approved": bool(human_approved),
            }, project_id=proposal["project_id"], actor_id=actor, status=decision_status,
        )
        if decision_status in {"accepted", "rejected", "superseded", "under_review"}:
            updated_payload = dict(proposal["payload"])
            updated_payload["latest_decision_id"] = decision["id"]
            updated = await store.update(
                "proposal", proposal_id, expected_version=proposal["version"],
                payload=updated_payload, status=decision_status,
            )
            if updated is None:
                raise HTTPException(status_code=409, detail="proposal changed concurrently; decision is retained in history")
        return {"ok": True, "decision": decision}

    @router.get("/projects/{project_id}/decisions")
    async def list_decisions(project_id: str, viewer_id: str, request: Request, limit: int = 100):
        await require_role(project_id, viewer_id(viewer_id, request), roles)
        return {"ok": True, "decisions": await store.list("decision", project_id=project_id, limit=limit)}

    @router.get("/projects/{project_id}/activity")
    async def project_activity(project_id: str, viewer_id: str, request: Request, actor_id: str | None = None, kind: str | None = None, status: str | None = None, limit: int = 100):
        await require_role(project_id, viewer_id(viewer_id, request), roles)
        records = await store.list(kind, project_id=project_id, actor_id=actor_id, status=status, limit=limit)
        return {"ok": True, "activity": records}

    return router
