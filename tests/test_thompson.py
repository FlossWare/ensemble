import asyncio

from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.store import InMemoryDocumentStore
from ensemble.thompson import select_model
from ensemble.thompson_store import SqliteThompsonStore, thompson_metadata


def test_thompson_selection_fallback_and_cost_filtering():
    assert select_model({"models": {}}) == "haiku"
    state = {
        "models": {
            "opus": {"successes": 10, "failures": 0, "calls": 10, "total_cost": 1.0},
            "haiku": {"successes": 1, "failures": 5, "calls": 6, "total_cost": 0.06},
        }
    }
    assert select_model(state, max_cost=0.02) == "haiku"
    # If all models exceed the limit, POC behavior falls back to the cheapest.
    assert select_model(state, max_cost=0.001) == "haiku"


def test_thompson_rest_actions_record_state_reset_and_ping():
    with TestClient(create_app(store=InMemoryDocumentStore())) as http:
        assert http.get("/api/v1/thompson/health").json()["service"] == "thompson"
        assert http.get("/api/v1/thompson/ready").json()["ready"] is True
        assert http.get("/api/v1/thompson/ping").json()["message"] == "pong"

        selected = http.post(
            "/api/v1/thompson/select",
            json={"task_type": "code_review", "required_capability": 0.7},
        )
        assert selected.status_code == 200
        assert selected.json()["model"] == "haiku"
        assert selected.json()["selection_id"]
        history = http.get("/api/v1/thompson/selections").json()["selections"]
        assert len(history) == 1
        assert history[0]["selection_id"] == selected.json()["selection_id"]
        assert history[0]["selected_model"] == "haiku"
        assert history[0]["task_type"] == "code_review"
        assert history[0]["required_capability"] == 0.7
        assert history[0]["max_cost"] is None

        recorded = http.post(
            "/api/v1/thompson/outcomes",
            json={"model": "haiku", "task_type": "code_review", "success": True, "cost": 0.01, "tokens": 100},
        )
        assert recorded.status_code == 200
        state = http.get("/api/v1/thompson/state").json()["state"]
        assert state["models"]["haiku"]["successes"] == 1
        assert state["models"]["haiku"]["calls"] == 1
        assert state["models"]["haiku"]["total_tokens"] == 100

        reset = http.post("/api/v1/thompson/reset", json={"model": "haiku"})
        assert reset.status_code == 200
        assert reset.json()["ok"] is True
        assert http.get("/api/v1/thompson/state").json()["state"]["models"]["haiku"]["calls"] == 0


def test_thompson_poc_action_contract_is_retained():
    with TestClient(create_app(store=InMemoryDocumentStore())) as http:
        selected = http.post("/api/v1/thompson", json={"action": "select_model", "task_type": "test"})
        assert selected.status_code == 200
        assert selected.json()["model"] == "haiku"
        state = http.post("/api/v1/thompson", json={"action": "get_state"})
        assert state.status_code == 200
        assert "models" in state.json()["state"]


def test_thompson_state_survives_store_restart(tmp_path):
    database = tmp_path / "thompson.sqlite"
    url = f"sqlite+aiosqlite:///{database}"

    async def exercise():
        store = SqliteThompsonStore(url)
        async with store.engine.begin() as connection:
            await connection.run_sync(thompson_metadata.create_all)
        await store.record_outcome("sonnet", True, 0.04, 1200)
        await store.record_outcome("sonnet", False, 0.02, 500)
        await store.record_selection("selection-0001", "sonnet", "code-review", 0.8, 0.05, "request-0001")
        await store.close()

        reopened = SqliteThompsonStore(url)
        state = await reopened.get_state()
        stats = state["models"]["sonnet"]
        assert stats["successes"] == 1
        assert stats["failures"] == 1
        assert stats["calls"] == 2
        assert stats["total_cost"] == 0.06
        assert stats["total_tokens"] == 1700
        history = await reopened.recent_selections(10)
        assert len(history) == 1
        assert history[0]["selection_id"] == "selection-0001"
        assert history[0]["selected_model"] == "sonnet"
        assert history[0]["max_cost"] == 0.05
        await reopened.close()

    asyncio.run(exercise())



def test_thompson_selection_history_limit_validation():
    with TestClient(create_app(store=InMemoryDocumentStore())) as http:
        assert http.get("/api/v1/thompson/selections?limit=0").status_code == 400
        selected = http.post("/api/v1/thompson/select", json={"task_type": "testing"})
        assert selected.status_code == 200
        assert len(http.get("/api/v1/thompson/selections?limit=1").json()["selections"]) == 1
