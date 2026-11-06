import asyncio

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.collaboration import InMemoryCollaborationStore, SqliteCollaborationStore
from ensemble.store import InMemoryDocumentStore


def test_collaboration_contributions_handoffs_synthesis_and_human_decisions():
    collaboration = InMemoryCollaborationStore()
    with TestClient(create_app(
        store=InMemoryDocumentStore(),
        collaboration_store=collaboration,
    )) as client:
        human = client.post("/api/v1/collaboration/participants", json={
            "participant_id": "human:owner",
            "display_name": "Project Owner",
            "participant_type": "human",
        })
        agent = client.post("/api/v1/collaboration/participants", json={
            "participant_id": "agent:assistant",
            "display_name": "Assistant",
            "participant_type": "agent",
            "provider": "provider-neutral",
            "model": "model-neutral",
        })
        assert human.status_code == agent.status_code == 200

        created = client.post("/api/v1/collaboration/projects", json={
            "name": "Shared project",
            "owner_id": "human:owner",
            "human_approval_required": True,
        })
        assert created.status_code == 200
        project_id = created.json()["project"]["id"]

        member = client.post(f"/api/v1/collaboration/projects/{project_id}/members", json={
            "actor_id": "human:owner",
            "participant_id": "agent:assistant",
            "role": "contribute",
        })
        assert member.status_code == 200

        contribution = client.post(f"/api/v1/collaboration/projects/{project_id}/contributions", json={
            "actor_id": "agent:assistant",
            "kind": "implementation",
            "summary": "Added a durable project capability.",
            "work_item_id": "issue-44",
            "source_ref": "https://github.com/example/repo/commit/abc123",
        })
        assert contribution.status_code == 200
        contribution_id = contribution.json()["contribution"]["id"]

        handoff = client.post(f"/api/v1/collaboration/projects/{project_id}/handoffs", json={
            "actor_id": "human:owner",
            "recipient_id": "agent:assistant",
            "work_item_id": "issue-45",
            "objective": "Continue the implementation.",
            "completed_work": "Created the persistence layer.",
            "current_state": "The API contract is ready.",
            "blockers": ["Awaiting integration tests"],
            "next_actions": ["Add lifecycle tests"],
            "references": [f"contribution:{contribution_id}"],
        })
        assert handoff.status_code == 200
        handoff_id = handoff.json()["handoff"]["id"]

        checkpoint = client.post(f"/api/v1/collaboration/handoffs/{handoff_id}/checkpoints", json={
            "actor_id": "agent:assistant",
            "current_state": "Implementation is complete; integration tests remain.",
            "completed_work": "Persisted the first implementation checkpoint.",
            "blockers": ["Integration test execution"],
            "next_actions": ["Run the full test suite"],
            "references": [f"contribution:{contribution_id}"],
        })
        assert checkpoint.status_code == 200
        assert checkpoint.json()["checkpoint"]["payload"]["current_state"].startswith("Implementation")

        acknowledged = client.post(f"/api/v1/collaboration/handoffs/{handoff_id}/acknowledge", json={
            "actor_id": "agent:assistant",
        })
        assert acknowledged.status_code == 200
        completed = client.post(f"/api/v1/collaboration/handoffs/{handoff_id}/complete", json={
            "actor_id": "agent:assistant",
        })
        assert completed.status_code == 200
        invalid = client.post(f"/api/v1/collaboration/handoffs/{handoff_id}/complete", json={
            "actor_id": "agent:assistant",
        })
        assert invalid.status_code == 409
        history = client.get(f"/api/v1/collaboration/handoffs/{handoff_id}")
        assert [item["payload"]["event"] for item in history.json()["history"]] == [
            "completed", "acknowledged", "checkpoint", "created",
        ]

        synthesis = client.post(f"/api/v1/collaboration/projects/{project_id}/syntheses", json={
            "actor_id": "human:owner",
            "contribution_ids": [contribution_id],
        })
        assert synthesis.status_code == 200
        statement = synthesis.json()["synthesis"]["payload"]["statements"][0]
        assert statement["contribution_ids"] == [contribution_id]
        assert statement["source_refs"] == [
            "https://github.com/example/repo/commit/abc123",
        ]

        refreshed = client.post(f"/api/v1/collaboration/syntheses/{synthesis.json()['synthesis']['id']}/refresh", json={
            "actor_id": "human:owner",
            "contribution_ids": [contribution_id],
        })
        assert refreshed.status_code == 200
        assert refreshed.json()["synthesis"]["id"] != synthesis.json()["synthesis"]["id"]
        assert refreshed.json()["synthesis"]["payload"]["supersedes_id"] == synthesis.json()["synthesis"]["id"]

        proposal = client.post(f"/api/v1/collaboration/projects/{project_id}/proposals", json={
            "actor_id": "agent:assistant",
            "title": "Adopt the new contract",
            "summary": "Use the shared contribution contract.",
            "rationale": "It preserves attribution and provenance.",
            "objections": ["Requires a migration"],
        })
        assert proposal.status_code == 200
        proposal_id = proposal.json()["proposal"]["id"]

        objection = client.post(f"/api/v1/collaboration/proposals/{proposal_id}/objections", json={
            "actor_id": "agent:assistant",
            "text": "The migration needs a rollback plan.",
            "contribution_ids": [contribution_id],
        })
        assert objection.status_code == 200
        proposal_detail = client.get(f"/api/v1/collaboration/proposals/{proposal_id}")
        assert len(proposal_detail.json()["objections"]) == 1

        agent_decision = client.post(f"/api/v1/collaboration/proposals/{proposal_id}/decisions", json={
            "actor_id": "agent:assistant",
            "status": "accepted",
            "rationale": "The agent recommends acceptance.",
        })
        assert agent_decision.status_code == 403

        human_decision = client.post(f"/api/v1/collaboration/proposals/{proposal_id}/decisions", json={
            "actor_id": "human:owner",
            "status": "accepted",
            "rationale": "Reviewed and approved by the project owner.",
        })
        assert human_decision.status_code == 200
        assert human_decision.json()["decision"]["payload"]["human_approved"] is True
        assert human_decision.json()["decision"]["status"] == "accepted"

        activity = client.get(f"/api/v1/collaboration/projects/{project_id}/activity", params={
            "actor_id": "agent:assistant",
            "kind": "contribution",
        })
        assert len(activity.json()["activity"]) == 1
        assert activity.json()["activity"][0]["id"] == contribution_id


def test_collaboration_contributions_remain_independent_under_concurrent_writes():
    store = InMemoryCollaborationStore()

    async def exercise():
        await store.create("project", {"name": "Concurrent"})
        project = (await store.list("project"))[0]

        async def add(index):
            return await store.create(
                "contribution",
                {"summary": f"contribution-{index}"},
                project_id=project["id"],
                actor_id=f"agent:{index}",
            )

        records = await asyncio.gather(*(add(index) for index in range(40)))
        listed = await store.list("contribution", project_id=project["id"], limit=100)
        assert len(records) == len(listed) == 40
        assert len({item["id"] for item in listed}) == 40

    asyncio.run(exercise())


def test_collaboration_handoff_updates_use_compare_and_swap():
    store = InMemoryCollaborationStore()

    async def exercise():
        handoff = await store.create(
            "handoff", {"objective": "continue"}, project_id="project-1",
            actor_id="human:1", status="open",
        )

        async def transition(status):
            return await store.update(
                "handoff", handoff["id"], expected_version=1,
                payload={"objective": "continue", "last_transition": status},
                status=status,
            )

        first, second = await asyncio.gather(
            transition("acknowledged"),
            transition("superseded"),
        )
        assert (first is None) != (second is None)

    asyncio.run(exercise())


def test_sqlite_collaboration_records_survive_restart_and_migrations(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'collaboration.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    async def exercise():
        first = SqliteCollaborationStore(database_url)
        await first.initialize()
        participant = await first.create(
            "participant",
            {"display_name": "Persistent Agent", "participant_type": "agent"},
            record_id="agent:persistent",
        )
        project = await first.create(
            "project",
            {"name": "Persistent project", "owner_id": participant["id"]},
        )
        await first.create(
            "membership",
            {"participant_id": participant["id"], "role": "owner"},
            project_id=project["id"], actor_id=participant["id"],
            identity_key=f"{project['id']}:{participant['id']}",
        )
        await first.close()

        second = SqliteCollaborationStore(database_url)
        await second.initialize()
        restored = await second.get("participant", "agent:persistent")
        members = await second.list("membership", project_id=project["id"])
        assert restored["payload"]["display_name"] == "Persistent Agent"
        assert len(members) == 1
        assert members[0]["payload"]["role"] == "owner"
        await second.close()

    asyncio.run(exercise())
